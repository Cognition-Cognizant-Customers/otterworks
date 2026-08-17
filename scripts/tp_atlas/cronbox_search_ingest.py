#!/usr/bin/env python3
"""Continuous ingest of the document/file corpus into the Atlas search collections.

The legacy weekly job deleted and recreated the MeiliSearch indexes on every
run. Here the collections are the searchable state and Atlas Search maintains
its indexes from the change stream, so ingest is a per-record idempotent upsert
and no rebuild, delete, or schedule exists. Records are transformed by pure
functions so the mapping can be proven against the local fixture corpus without
touching Atlas.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

DATABASE = "ow_tp_cronbox_demo"
URI_ENV = "MONGODB_ATLAS_URI"
DOCUMENTS = "documents"
FILES = "files"

# Analyzed (searchable) fields per collection, mirroring the legacy
# searchableAttributes. A field that is not valid UTF-8 is stored as binary
# under "<field>__binary" and omitted from these paths.
ANALYZED_FIELDS = {
    DOCUMENTS: ("title", "content", "tags"),
    FILES: ("name", "tags", "mime_type"),
}


@dataclass(frozen=True)
class Attribution:
    """A record the migrated corpus refused to index, with its source position."""

    collection: str
    source_position: int
    source_id: str
    reason: str


@dataclass
class TransformResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    attributions: list[Attribution] = field(default_factory=list)

    def as_report(self) -> dict[str, Any]:
        return {
            "indexed": len(self.records),
            "attributed": len(self.attributions),
            "attributions": [asdict(item) for item in self.attributions],
        }


def _text(value: Any, name: str, out: dict[str, Any]) -> Any:
    """Store a text value byte-transparently; binary values leave the analyzed path."""
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            from bson import Binary

            out[f"{name}__binary"] = Binary(bytes(value))
            return None
    return value


def _timestamp(value: Any) -> datetime | None:
    """Normalize an ISO-8601 source timestamp to a UTC BSON date."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        text = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tags(value: Any, out: dict[str, Any]) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        value = [value]
    tags = []
    for position, item in enumerate(value):
        text = _text(item, f"tags.{position}", out)
        if text is not None:
            tags.append(text)
    return tags


def _identifier(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, (bytes, bytearray)):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                continue
        if isinstance(value, str) and value.strip():
            return value
    return ""


def transform_document(
    record: Mapping[str, Any], position: int
) -> dict[str, Any] | Attribution:
    identifier = _identifier(record, "document_id", "id")
    if not identifier:
        return Attribution(DOCUMENTS, position, "", "missing_or_empty_id")
    out: dict[str, Any] = {"_id": identifier, "id": identifier, "type": "document"}
    out["title"] = _text(record.get("title", ""), "title", out)
    out["content"] = _text(record.get("content", ""), "content", out)
    out["owner_id"] = _identifier(record, "owner_id")
    out["tags"] = _tags(record.get("tags"), out)
    out["created_at"] = _timestamp(record.get("created_at"))
    out["updated_at"] = _timestamp(record.get("updated_at"))
    return {key: value for key, value in out.items() if value is not None}


def transform_file(
    record: Mapping[str, Any], position: int
) -> dict[str, Any] | Attribution:
    identifier = _identifier(record, "file_id", "id")
    if not identifier:
        return Attribution(FILES, position, "", "missing_or_empty_id")
    out: dict[str, Any] = {"_id": identifier, "id": identifier, "type": "file"}
    out["name"] = _text(record.get("file_name", record.get("name", "")), "name", out)
    out["owner_id"] = _identifier(record, "owner_id")
    out["mime_type"] = _text(record.get("mime_type", ""), "mime_type", out)
    out["folder_id"] = _identifier(record, "folder_id")
    size = record.get("size_bytes", record.get("size", 0))
    try:
        out["size"] = int(size)
    except (TypeError, ValueError):
        out["size"] = 0
    out["tags"] = _tags(record.get("tags"), out)
    out["created_at"] = _timestamp(record.get("created_at"))
    out["updated_at"] = _timestamp(record.get("updated_at"))
    return {key: value for key, value in out.items() if value is not None}


def transform(collection: str, records: Iterable[Mapping[str, Any]]) -> TransformResult:
    transformer = transform_document if collection == DOCUMENTS else transform_file
    result = TransformResult()
    for position, record in enumerate(records):
        outcome = transformer(record, position)
        if isinstance(outcome, Attribution):
            result.attributions.append(outcome)
        else:
            result.records.append(outcome)
    return result


def fetch_corpus(
    base_url: str, path: str, key: str, page_size: int = 100
) -> Iterator[dict[str, Any]]:
    """Page the source-of-truth HTTP API, exactly the corpus the legacy job read."""
    import requests

    page = 1
    while True:
        response = requests.get(
            f"{base_url.rstrip('/')}{path}",
            params={"page": page, "size": page_size, "page_size": page_size},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload.get(key, payload.get("items", []))
        if not batch:
            return
        yield from batch
        if len(batch) < page_size:
            return
        page += 1


def upsert(collection: Any, records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Idempotent per-record upsert: no index is ever deleted or rebuilt."""
    from pymongo import ReplaceOne

    if not records:
        return {"matched": 0, "upserted": 0}
    result = collection.bulk_write(
        [
            ReplaceOne({"_id": record["_id"]}, dict(record), upsert=True)
            for record in records
        ],
        ordered=False,
    )
    return {"matched": result.matched_count, "upserted": len(result.upserted_ids or {})}


def _require_uri() -> str:
    uri = os.environ.get(URI_ENV)
    if not uri:
        raise SystemExit(f"{URI_ENV} is required for an Atlas write")
    return uri


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", choices=(DOCUMENTS, FILES), required=True)
    parser.add_argument(
        "--source-url",
        help="base URL of the source-of-truth API (document-service or file-service)",
    )
    parser.add_argument(
        "--source-file",
        help="newline-delimited JSON records, or '-' for stdin; used for per-record upserts",
    )
    parser.add_argument("--database", default=DATABASE)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="transform only and print the attribution report; never connects to Atlas",
    )
    args = parser.parse_args(argv)

    if args.source_url:
        path, key = (
            ("/api/v1/documents", "documents")
            if args.collection == DOCUMENTS
            else ("/api/v1/files", "files")
        )
        source: Iterable[Mapping[str, Any]] = fetch_corpus(args.source_url, path, key)
    elif args.source_file:
        stream = (
            sys.stdin
            if args.source_file == "-"
            else open(args.source_file, encoding="utf-8")
        )
        try:
            source = [json.loads(line) for line in stream if line.strip()]
        finally:
            if stream is not sys.stdin:
                stream.close()
    else:
        parser.error("one of --source-url or --source-file is required")

    result = transform(args.collection, source)
    report = result.as_report()

    if not args.dry_run:
        from pymongo import MongoClient

        client = MongoClient(_require_uri(), serverSelectionTimeoutMS=10_000)
        try:
            report["write"] = upsert(
                client[args.database][args.collection], result.records
            )
        finally:
            client.close()

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

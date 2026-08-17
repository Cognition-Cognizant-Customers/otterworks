#!/usr/bin/env python3
"""Migrate the legacy Postgres document estate into the local Mongo fixture."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    DOCUMENTS,
    QUARANTINE,
    SNAPSHOTS,
    VERSION_ARRAY_BOUND,
    mongo_client,
    pg_connect,
    source_schema,
    target_db_name,
)
from pymongo import ASCENDING, ReplaceOne
from recon_documents import missing_versions

BATCH_SIZE = 500


def utc_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("expected a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def decode_text(value: Any) -> tuple[Any, str | None]:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        try:
            return raw.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, raw.hex()
    return value, None


def source_id_text(value: Any) -> str:
    decoded, raw_hex = decode_text(value)
    if raw_hex is not None:
        return f"bytes:{raw_hex}"
    if decoded is None:
        return "<null>"
    return str(decoded)


def raw_value(value: Any) -> Any:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, datetime):
        try:
            return utc_datetime(value).isoformat()
        except ValueError:
            return str(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def raw_record(columns: tuple[str, ...], row: tuple[Any, ...]) -> dict[str, Any]:
    return {column: raw_value(value) for column, value in zip(columns, row)}


def quarantine_record(
    ns: str,
    source_table: str,
    source_id: Any,
    reason: str,
    detail: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    source_id_value = source_id_text(source_id)
    return {
        "_id": f"{source_table}:{source_id_value}",
        "ns": ns,
        "source_table": source_table,
        "source_id": source_id_value,
        "reason": reason,
        "detail": detail,
        "raw": raw,
    }


def replace_batch(collection, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    collection.bulk_write(
        [ReplaceOne({"_id": record["_id"]}, record, upsert=True) for record in records],
        ordered=False,
    )


STRING = {"bsonType": "string"}
INTEGER = {"bsonType": ["int", "long"]}
DATE = {"bsonType": "date"}

VERSION_SCHEMA = {
    "bsonType": "object",
    "required": [
        "_id",
        "version_number",
        "title",
        "content",
        "created_by",
        "created_at",
    ],
    "properties": {
        "_id": STRING,
        "version_number": INTEGER,
        "title": STRING,
        "content": STRING,
        "created_by": STRING,
        "created_at": DATE,
    },
    "additionalProperties": False,
}

DOCUMENTS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_id",
            "ns",
            "title",
            "content",
            "content_type",
            "owner_id",
            "is_deleted",
            "is_template",
            "word_count",
            "declared_version",
            "created_at",
            "updated_at",
            "versions",
            "version_sequence",
        ],
        "properties": {
            "_id": STRING,
            "ns": STRING,
            "title": STRING,
            "content": STRING,
            "content_type": STRING,
            "owner_id": STRING,
            "folder_id": STRING,
            "is_deleted": {"bsonType": "bool"},
            "is_template": {"bsonType": "bool"},
            "word_count": INTEGER,
            "declared_version": INTEGER,
            "created_at": DATE,
            "updated_at": DATE,
            "versions": {
                "bsonType": "array",
                "items": VERSION_SCHEMA,
            },
            "version_sequence": {
                "bsonType": "object",
                "required": ["declared", "present", "missing"],
                "properties": {
                    "declared": INTEGER,
                    "present": INTEGER,
                    "missing": {
                        "bsonType": "array",
                        "items": INTEGER,
                    },
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
}

SNAPSHOTS_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_id",
            "ns",
            "document_id",
            "state_b64",
            "created_by",
            "created_at",
            "parent_missing",
        ],
        "properties": {
            "_id": STRING,
            "ns": STRING,
            "document_id": STRING,
            "state_b64": STRING,
            "label": STRING,
            "created_by": STRING,
            "created_at": DATE,
            "parent_missing": {"bsonType": "bool"},
        },
        "additionalProperties": False,
    }
}

QUARANTINE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "ns", "source_table", "source_id", "reason", "detail"],
        "properties": {
            "_id": STRING,
            "ns": STRING,
            "source_table": STRING,
            "source_id": STRING,
            "reason": STRING,
            "detail": STRING,
            "raw": {"bsonType": "object"},
        },
        "additionalProperties": False,
    }
}

VALIDATORS = {
    DOCUMENTS: DOCUMENTS_VALIDATOR,
    SNAPSHOTS: SNAPSHOTS_VALIDATOR,
    QUARANTINE: QUARANTINE_VALIDATOR,
}

DOCUMENT_COLUMNS = (
    "id",
    "title",
    "content",
    "content_type",
    "owner_id",
    "folder_id",
    "is_deleted",
    "is_template",
    "word_count",
    "version",
    "created_at",
    "updated_at",
)
VERSION_COLUMNS = (
    "id",
    "document_id",
    "version_number",
    "title",
    "content",
    "created_by",
    "created_at",
)
SNAPSHOT_COLUMNS = (
    "id",
    "document_id",
    "state_b64",
    "label",
    "created_by",
    "created_at",
)


def ensure_target(db) -> None:
    existing = set(db.list_collection_names())
    for name, validator in VALIDATORS.items():
        if name not in existing:
            db.create_collection(
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )
        else:
            db.command(
                "collMod",
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )

    db[DOCUMENTS].create_index([("owner_id", ASCENDING)])
    db[DOCUMENTS].create_index([("ns", ASCENDING)])
    db[SNAPSHOTS].create_index([("document_id", ASCENDING)])
    db[SNAPSHOTS].create_index([("parent_missing", ASCENDING)])


def document_id_from_row(row: tuple[Any, ...]) -> str | None:
    value = row[0]
    if value is None:
        return None
    decoded, raw_hex = decode_text(value)
    if raw_hex is not None:
        return f"bytes:{raw_hex}"
    return str(decoded)


def process_version(
    ns: str,
    row: tuple[Any, ...],
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    raw = raw_record(VERSION_COLUMNS, row)
    source_id = row[0]
    source_key = source_id_text(source_id)
    required = {
        "id": row[0],
        "document_id": row[1],
        "version_number": row[2],
        "title": row[3],
        "content": row[4],
        "created_by": row[5],
        "created_at": row[6],
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return (
            str(row[1]) if row[1] is not None else "<null>",
            None,
            quarantine_record(
                ns,
                "document_versions",
                source_id,
                "null_required_field",
                f"required field(s) are NULL: {', '.join(missing)}",
                raw,
            ),
        )

    title, title_hex = decode_text(row[3])
    content, content_hex = decode_text(row[4])
    encoding_errors = [
        name
        for name, value in (("title", title_hex), ("content", content_hex))
        if value is not None
    ]
    if encoding_errors:
        return (
            str(row[1]),
            None,
            quarantine_record(
                ns,
                "document_versions",
                source_id,
                "invalid_utf8",
                f"invalid UTF-8 in field(s): {', '.join(encoding_errors)}; raw bytes are hex-encoded",
                raw,
            ),
        )

    try:
        created_at = utc_datetime(row[6])
    except ValueError as exc:
        return (
            str(row[1]),
            None,
            quarantine_record(
                ns,
                "document_versions",
                source_id,
                "invalid_value",
                f"created_at: {exc}",
                raw,
            ),
        )

    return (
        str(row[1]),
        {
            "_id": source_key,
            "version_number": int(row[2]),
            "title": title,
            "content": content,
            "created_by": str(row[5]),
            "created_at": created_at,
        },
        None,
    )


def process_document(
    ns: str,
    row: tuple[Any, ...],
    version_rows: list[dict[str, Any] | None],
) -> tuple[dict[str, Any] | None, dict[str, Any], bool]:
    raw = raw_record(DOCUMENT_COLUMNS, row)
    source_id = row[0]
    required = {
        "id": row[0],
        "title": row[1],
        "content": row[2],
        "content_type": row[3],
        "owner_id": row[4],
        "is_deleted": row[6],
        "is_template": row[7],
        "word_count": row[8],
        "version": row[9],
        "created_at": row[10],
        "updated_at": row[11],
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        return (
            None,
            quarantine_record(
                ns,
                "documents",
                source_id,
                "null_required_field",
                f"required field(s) are NULL: {', '.join(missing)}",
                raw,
            ),
            False,
        )

    title, title_hex = decode_text(row[1])
    content, content_hex = decode_text(row[2])
    content_type, content_type_hex = decode_text(row[3])
    folder_id, folder_hex = decode_text(row[5])
    encoding_errors = [
        name
        for name, value in (
            ("title", title_hex),
            ("content", content_hex),
            ("content_type", content_type_hex),
            ("folder_id", folder_hex),
        )
        if value is not None
    ]
    if encoding_errors:
        return (
            None,
            quarantine_record(
                ns,
                "documents",
                source_id,
                "invalid_utf8",
                f"invalid UTF-8 in field(s): {', '.join(encoding_errors)}; raw bytes are hex-encoded",
                raw,
            ),
            False,
        )

    try:
        created_at = utc_datetime(row[10])
        updated_at = utc_datetime(row[11])
    except ValueError as exc:
        return (
            None,
            quarantine_record(
                ns,
                "documents",
                source_id,
                "invalid_value",
                str(exc),
                raw,
            ),
            False,
        )

    valid_versions = [version for version in version_rows if version is not None]
    if len(version_rows) > VERSION_ARRAY_BOUND:
        return (
            None,
            quarantine_record(
                ns,
                "documents",
                source_id,
                "version_array_over_bound",
                f"{len(version_rows)} versions exceed bound {VERSION_ARRAY_BOUND}",
                raw,
            ),
            False,
        )

    valid_versions.sort(key=lambda version: version["version_number"])
    present = [version["version_number"] for version in valid_versions]
    missing_versions = missing_versions_for(int(row[9]), present)
    document = {
        "_id": source_id_text(source_id),
        "ns": ns,
        "title": title,
        "content": content,
        "content_type": content_type,
        "owner_id": str(row[4]),
        "is_deleted": bool(row[6]),
        "is_template": bool(row[7]),
        "word_count": int(row[8]),
        "declared_version": int(row[9]),
        "created_at": created_at,
        "updated_at": updated_at,
        "versions": valid_versions,
        "version_sequence": {
            "declared": int(row[9]),
            "present": len(present),
            "missing": missing_versions,
        },
    }
    if folder_id is not None:
        document["folder_id"] = str(folder_id)
    return document, None, bool(missing_versions)


def missing_versions_for(declared: int, present: list[int]) -> list[int]:
    return missing_versions(declared, present)


def migrate(ns: str) -> dict[str, int]:
    schema = source_schema(ns)
    conn = pg_connect(ns)
    client = None
    documents_written = 0
    versions_embedded = 0
    snapshots_written = 0
    quarantined = 0
    gaps_detected = 0
    orphans_detected = 0
    try:
        document_cursor = conn.cursor(name="ow_tp_documents")
        document_cursor.itersize = BATCH_SIZE
        document_cursor.execute(
            f"""
            SELECT id, title, content, content_type, owner_id, folder_id,
                   is_deleted, is_template, word_count, version,
                   created_at, updated_at
              FROM {schema}.documents
             ORDER BY id
            """
        )
        first_batch = document_cursor.fetchmany(BATCH_SIZE)
        if not first_batch:
            raise RuntimeError(
                f"source read returned zero documents for {schema}.documents; "
                "no target writes were attempted"
            )

        client = mongo_client()
        client.admin.command("ping")
        db = client[target_db_name(ns)]
        ensure_target(db)

        source_document_ids: set[str] = set()
        document_collection = db[DOCUMENTS]
        quarantine_collection = db[QUARANTINE]

        batch = first_batch
        while batch:
            for row in batch:
                document_id = document_id_from_row(row)
                if document_id is not None:
                    source_document_ids.add(document_id)

            document_ids = [
                document_id_from_row(row)
                for row in batch
                if document_id_from_row(row) is not None
            ]
            versions_by_document: dict[str, list[dict[str, Any] | None]] = defaultdict(list)
            version_quarantine: list[dict[str, Any]] = []
            version_cursor = conn.cursor(name=f"ow_tp_versions_{documents_written}")
            version_cursor.itersize = BATCH_SIZE
            version_cursor.execute(
                f"""
                SELECT id, document_id, version_number, title, content,
                       created_by, created_at
                  FROM {schema}.document_versions
                 WHERE document_id = ANY(%s::uuid[])
                 ORDER BY document_id, version_number
                """,
                (document_ids,),
            )
            for version_row in version_cursor:
                document_id, version, quarantine = process_version(ns, version_row)
                versions_by_document[document_id].append(version)
                if quarantine is not None:
                    version_quarantine.append(quarantine)
            version_cursor.close()

            document_records: list[dict[str, Any]] = []
            document_quarantine: list[dict[str, Any]] = []
            for row in batch:
                document_id = document_id_from_row(row) or "<null>"
                document, quarantine, has_gap = process_document(
                    ns,
                    row,
                    versions_by_document.get(document_id, []),
                )
                if document is not None:
                    document_records.append(document)
                    documents_written += 1
                    versions_embedded += len(document["versions"])
                    gaps_detected += int(has_gap)
                elif quarantine is not None:
                    document_quarantine.append(quarantine)

            replace_batch(document_collection, document_records)
            replace_batch(
                quarantine_collection,
                version_quarantine + document_quarantine,
            )
            quarantined += len(version_quarantine) + len(document_quarantine)
            document_cursor_batch = document_cursor.fetchmany(BATCH_SIZE)
            batch = document_cursor_batch
        document_cursor.close()

        snapshot_cursor = conn.cursor(name="ow_tp_snapshots")
        snapshot_cursor.itersize = BATCH_SIZE
        snapshot_cursor.execute(
            f"""
            SELECT id, document_id, state_b64, label, created_by, created_at
              FROM {schema}.document_snapshots
             ORDER BY id
            """
        )
        snapshot_records: list[dict[str, Any]] = []
        snapshot_quarantine: list[dict[str, Any]] = []
        for snapshot_row in snapshot_cursor:
            raw = raw_record(SNAPSHOT_COLUMNS, snapshot_row)
            source_id = snapshot_row[0]
            required = {
                "id": snapshot_row[0],
                "document_id": snapshot_row[1],
                "state_b64": snapshot_row[2],
                "created_by": snapshot_row[4],
                "created_at": snapshot_row[5],
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                snapshot_quarantine.append(
                    quarantine_record(
                        ns,
                        "document_snapshots",
                        source_id,
                        "null_required_field",
                        f"required field(s) are NULL: {', '.join(missing)}",
                        raw,
                    )
                )
                continue

            state_b64, state_hex = decode_text(snapshot_row[2])
            label, label_hex = decode_text(snapshot_row[3])
            encoding_errors = [
                name
                for name, value in (("state_b64", state_hex), ("label", label_hex))
                if value is not None
            ]
            if encoding_errors:
                snapshot_quarantine.append(
                    quarantine_record(
                        ns,
                        "document_snapshots",
                        source_id,
                        "invalid_utf8",
                        f"invalid UTF-8 in field(s): {', '.join(encoding_errors)}; raw bytes are hex-encoded",
                        raw,
                    )
                )
                continue

            try:
                created_at = utc_datetime(snapshot_row[5])
            except ValueError as exc:
                snapshot_quarantine.append(
                    quarantine_record(
                        ns,
                        "document_snapshots",
                        source_id,
                        "invalid_value",
                        str(exc),
                        raw,
                    )
                )
                continue

            document_id = str(snapshot_row[1])
            snapshot = {
                "_id": source_id_text(source_id),
                "ns": ns,
                "document_id": document_id,
                "state_b64": state_b64,
                "created_by": str(snapshot_row[4]),
                "created_at": created_at,
                "parent_missing": document_id not in source_document_ids,
            }
            if label is not None:
                snapshot["label"] = str(label)
            snapshot_records.append(snapshot)
            if snapshot["parent_missing"]:
                orphans_detected += 1
            if len(snapshot_records) >= BATCH_SIZE:
                replace_batch(db[SNAPSHOTS], snapshot_records)
                replace_batch(quarantine_collection, snapshot_quarantine)
                snapshots_written += len(snapshot_records)
                quarantined += len(snapshot_quarantine)
                snapshot_records = []
                snapshot_quarantine = []

        snapshot_cursor.close()
        replace_batch(db[SNAPSHOTS], snapshot_records)
        replace_batch(quarantine_collection, snapshot_quarantine)
        snapshots_written += len(snapshot_records)
        quarantined += len(snapshot_quarantine)
        return {
            "documents": documents_written,
            "versions": versions_embedded,
            "snapshots": snapshots_written,
            "quarantined": quarantined,
            "gaps": gaps_detected,
            "orphans": orphans_detected,
        }
    finally:
        if client is not None:
            client.close()
        conn.rollback()
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", default="demo")
    args = parser.parse_args()
    try:
        summary = migrate(args.ns)
    except RuntimeError as exc:
        print(f"[migration] error: {exc}", file=sys.stderr)
        return 1
    print(
        f"[migration] ns={args.ns} target={target_db_name(args.ns)} "
        f"documents written={summary['documents']} "
        f"versions embedded={summary['versions']} "
        f"snapshots written={summary['snapshots']} "
        f"quarantined={summary['quarantined']} "
        f"gaps detected={summary['gaps']} "
        f"orphans detected={summary['orphans']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

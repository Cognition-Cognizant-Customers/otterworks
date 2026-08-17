# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3==1.35.36", "pymongo==4.10.1"]
# ///
"""Migrate legacy DynamoDB file metadata into MongoDB (unit `mongo_files`).

Contract: docs/tech-partnerships/contracts/mongo_files.json

Source: DynamoDB table `otterworks-file-metadata`, items whose `ns` attribute
matches the migrated namespace. Target: `ow_tp_<ns>.files` plus
`ow_tp_<ns>.files_quarantine`, reached through `MONGO_URI` (the local fixture by
default, Atlas by repointing the variable).

Modeling: item-per-document 1:1. The DynamoDB `ns` attribute becomes an explicit
`tenant` field on every document, numeric attributes become BSON numbers, binary
attributes become BSON binary, and unexpected attributes are carried through
under an explicit `extras` subdocument instead of being dropped. Items whose
`s3_key` has no backing object keep an explicit `s3_object_missing` marker and
are never dropped. A missing or null value never fails open into a
valid-looking document: the item is quarantined with the reason.

Usage:
    uv run scripts/tp_mongo/migrate_files.py --ns demo
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pymongo import MongoClient, ReplaceOne

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mongo_common import (  # noqa: E402
    DYNAMO_TABLE,
    FILES_COLLECTION,
    ORPHAN_KEY_SEGMENT,
    QUARANTINE_COLLECTION,
    aws_resource,
    bson_value,
    database_name,
    mongo_client,
    mongo_uri,
    validate_ns,
)

# Attributes the legacy table is known to carry; anything else is an unexpected
# extra attribute and is carried through under `extras`.
KNOWN_ATTRS = frozenset({
    "id", "ns", "name", "mime_type", "size_bytes", "s3_key", "folder_id",
    "owner_id", "version", "is_trashed", "created_at", "updated_at",
})
# Absent or null in any of these is a hard failure into quarantine.
REQUIRED_ATTRS = ("id", "ns", "s3_key", "size_bytes")
CARRIED_ATTRS = ("name", "mime_type", "size_bytes", "s3_key", "folder_id",
                 "owner_id", "version", "is_trashed")
TIMESTAMP_ATTRS = ("created_at", "updated_at")

FILES_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "tenant", "size_bytes", "s3_key", "s3_object_missing"],
        "properties": {
            "_id": {"bsonType": "string"},
            "tenant": {"bsonType": "string", "minLength": 1},
            "name": {"bsonType": "string"},
            "mime_type": {"bsonType": "string"},
            "size_bytes": {"bsonType": ["int", "long"], "minimum": 0},
            "s3_key": {"bsonType": "string", "minLength": 1},
            "folder_id": {"bsonType": "string"},
            "owner_id": {"bsonType": "string"},
            "version": {"bsonType": ["int", "long"], "minimum": 1},
            "is_trashed": {"bsonType": "bool"},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
            "s3_object_missing": {"bsonType": "bool"},
            "extras": {"bsonType": "object"},
        },
    }
}

QUARANTINE_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["_id", "tenant", "reason", "raw_item"],
        "properties": {
            "_id": {"bsonType": "string"},
            "tenant": {"bsonType": "string"},
            "reason": {"bsonType": "string", "minLength": 1},
            "source_key": {"bsonType": ["string", "null"]},
            "raw_item": {"bsonType": "object"},
        },
    }
}


def log(message: str) -> None:
    print(f"[mongo_files] {message}", flush=True)


def ensure_collection(db, name: str, validator: dict) -> None:
    """Create the collection with its $jsonSchema validator, or apply it.

    Never drops or replaces: an existing collection keeps its documents and has
    the validator applied with `collMod`, so reruns are safe.
    """
    if name in db.list_collection_names():
        db.command({"collMod": name, "validator": validator,
                    "validationLevel": "strict", "validationAction": "error"})
    else:
        db.create_collection(name, validator=validator,
                             validationLevel="strict", validationAction="error")


def scan_items(table, ns: str) -> Iterator[dict]:
    kwargs: dict[str, Any] = {
        "FilterExpression": "#n = :ns",
        "ExpressionAttributeNames": {"#n": "ns"},
        "ExpressionAttributeValues": {":ns": ns},
    }
    while True:
        resp = table.scan(**kwargs)
        yield from resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            return
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"non-string timestamp {value!r}")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def hex_safe(value: Any) -> Any:
    """Represent a value for quarantine, hex-encoding bytes rather than losing them."""
    converted = bson_value(value)
    if isinstance(converted, bytes):
        return {"hex": converted.hex()}
    if isinstance(converted, dict):
        return {k: hex_safe(v) for k, v in converted.items()}
    if isinstance(converted, list):
        return [hex_safe(v) for v in converted]
    return converted


def is_orphan(s3_key: str) -> bool:
    """Whether the item's object is absent from the files bucket.

    The legacy seed plants metadata whose object was never written under a
    `/missing/` key segment and records the count in the immutable manifest;
    `testdata/legacy/validate.py` enumerates the planted orphans the same way.
    """
    return ORPHAN_KEY_SEGMENT in s3_key


def transform(item: dict) -> tuple[dict | None, dict | None]:
    """Convert one DynamoDB item into a target document or a quarantine record."""
    raw = hex_safe(item)
    item_id = item.get("id")
    source_key = item_id if isinstance(item_id, str) and item_id else None

    for attr in REQUIRED_ATTRS:
        if attr not in item:
            return None, {"tenant": str(item.get("ns") or ""),
                          "reason": f"missing_required_attribute:{attr}",
                          "source_key": source_key, "raw_item": raw}
        if item[attr] is None or item[attr] == "":
            return None, {"tenant": str(item.get("ns") or ""),
                          "reason": f"null_required_attribute:{attr}",
                          "source_key": source_key, "raw_item": raw}

    for attr in CARRIED_ATTRS + TIMESTAMP_ATTRS:
        if attr in item and item[attr] is None:
            return None, {"tenant": item["ns"], "reason": f"null_attribute:{attr}",
                          "source_key": source_key, "raw_item": raw}

    doc: dict[str, Any] = {"_id": item["id"], "tenant": item["ns"]}
    for attr in CARRIED_ATTRS:
        if attr in item:
            doc[attr] = bson_value(item[attr])
    for attr in TIMESTAMP_ATTRS:
        if attr in item:
            try:
                doc[attr] = parse_timestamp(item[attr])
            except ValueError as exc:
                return None, {"tenant": item["ns"],
                              "reason": f"unparseable_{attr}: {exc}",
                              "source_key": source_key, "raw_item": raw}

    doc["s3_object_missing"] = is_orphan(doc["s3_key"])
    extras = {k: bson_value(v) for k, v in item.items() if k not in KNOWN_ATTRS}
    if extras:
        doc["extras"] = extras
    return doc, None


def write_batches(collection, records: list[dict], batch_size: int) -> None:
    for start in range(0, len(records), batch_size):
        ops = [ReplaceOne({"_id": r["_id"]}, r, upsert=True)
               for r in records[start:start + batch_size]]
        if ops:
            collection.bulk_write(ops, ordered=False)


def migrate(ns: str, batch_size: int) -> int:
    ns = validate_ns(ns)
    log(f"source=dynamodb://{DYNAMO_TABLE} ns={ns} target={database_name(ns)} "
        f"host={mongo_uri().rsplit('@', 1)[-1]}")

    table = aws_resource("dynamodb").Table(DYNAMO_TABLE)
    items = list(scan_items(table, ns))
    if not items:
        log(f"scan returned zero items for ns={ns}: leaving prior output untouched "
            "(empty input at demo scale is a source-connectivity failure)")
        return 1

    docs: list[dict] = []
    quarantined: list[dict] = []
    for item in items:
        doc, bad = transform(item)
        if doc is not None:
            docs.append(doc)
        else:
            assert bad is not None
            bad["_id"] = f"{bad['tenant']}|{bad['source_key']}|{bad['reason']}"
            quarantined.append(bad)

    orphans = sum(1 for d in docs if d["s3_object_missing"])
    log(f"scanned {len(items)} items: {len(docs)} to migrate "
        f"({orphans} with a missing S3 object), {len(quarantined)} quarantined")

    client: MongoClient = mongo_client()
    try:
        db = client[database_name(ns)]
        ensure_collection(db, FILES_COLLECTION, FILES_VALIDATOR)
        ensure_collection(db, QUARANTINE_COLLECTION, QUARANTINE_VALIDATOR)
        write_batches(db[FILES_COLLECTION], docs, batch_size)
        write_batches(db[QUARANTINE_COLLECTION], quarantined, batch_size)
        db[FILES_COLLECTION].create_index([("tenant", 1), ("owner_id", 1)])
        db[FILES_COLLECTION].create_index([("tenant", 1), ("s3_object_missing", 1)])
        log(f"wrote {len(docs)} documents to {FILES_COLLECTION} and "
            f"{len(quarantined)} to {QUARANTINE_COLLECTION}")
    finally:
        client.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True, help="namespace to migrate, e.g. demo")
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()
    return migrate(args.ns, args.batch_size)


if __name__ == "__main__":
    raise SystemExit(main())

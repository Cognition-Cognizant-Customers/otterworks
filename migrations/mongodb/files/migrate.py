# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3", "pymongo"]
# ///
"""mongo_files unit: DynamoDB otterworks-file-metadata -> MongoDB files collection.

Contract: docs/tech-partnerships/contracts/mongo_files.json.

Reads items where ns=<ns> from the shared DynamoDB table (LocalStack in the
fixture phase) and writes them 1:1 item-per-document into
ow_tp_mongodb_<ns>.files, mapping the `ns` attribute to a `tenant` field.
Items whose s3_key carries the estate's orphan marker (a `/missing/` path
segment: the key points at no seeded object in s3://otterworks-data-lake, the
same detector testdata/legacy/validate.py uses) are flagged in place and
quarantined with attribution into
ow_tp_mongodb_<ns>_quarantine.files_quarantine.

Policies (from the contract):
  - byte transparency: attribute values carried as-is; DynamoDB Binary
    attributes become BSON binary (bytes) unmodified.
  - malformed records: NULL attributes are omitted (never fabricated) and
    attributed on the document; unknown extra attributes are carried through
    and attributed. Nothing fails open into a valid-looking field.
  - empty input: a run that finds no items for the namespace writes nothing
    and leaves prior target output untouched.
  - granularity: per-batch — each DynamoDB scan page is committed as one
    idempotent bulk of _id upserts, so a rerun reproduces identical output.

Usage:
    uv run migrations/mongodb/files/migrate.py --ns <ns> [--mongo-uri URI]

The target is addressed only via --mongo-uri / MONGODB_URI (default
mongodb://localhost:27017) so the same code runs against a local fixture now
and Atlas in the parent's live validation window later.
"""

import argparse
import os
import re
import sys
from decimal import Decimal

import boto3
from boto3.dynamodb.types import Binary
from pymongo import MongoClient, ReplaceOne

DYNAMO_TABLE = "otterworks-file-metadata"
ORPHAN_MARKER = "/missing/"

# Attributes written by the estate's seeder (testdata/legacy/seed.py); anything
# else on an item is carried through and attributed as unknown.
KNOWN_ATTRIBUTES = {
    "id", "ns", "name", "mime_type", "size_bytes", "s3_key", "folder_id",
    "owner_id", "version", "is_trashed", "created_at", "updated_at",
}

# Fields the migration itself owns on the output document; a source attribute
# with one of these names is carried under attributed.reserved_name_collisions
# instead of overwriting or being clobbered by the migration's own values.
RESERVED_FIELDS = {"_id", "tenant", "attributed", "flags"}

NS_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def log(msg: str) -> None:
    print(f"[migrate-files] {msg}", flush=True)


def to_bson(value):
    """Carry DynamoDB values into BSON byte-for-byte."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Binary):
        return bytes(value)
    if isinstance(value, list):
        return [to_bson(v) for v in value]
    if isinstance(value, set):
        return sorted(to_bson(v) for v in value)
    if isinstance(value, dict):
        return {k: to_bson(v) for k, v in value.items()}
    return value


def transform(item: dict, ns: str) -> tuple[dict, dict | None]:
    """Map one DynamoDB item to (files document, quarantine record | None)."""
    doc: dict = {"_id": item["id"], "tenant": ns}
    null_attributes: list[str] = []
    unknown_attributes: list[str] = []
    reserved_collisions: dict = {}

    for key, value in item.items():
        if key in ("id", "ns"):
            continue
        if value is None:
            null_attributes.append(key)  # omitted, attributed — never fails open
            continue
        if key in RESERVED_FIELDS:
            reserved_collisions[key] = to_bson(value)  # carried, never overwrites
            continue
        if key not in KNOWN_ATTRIBUTES:
            unknown_attributes.append(key)
        doc[key] = to_bson(value)

    attributed = {}
    if null_attributes:
        attributed["null_attributes"] = sorted(null_attributes)
    if unknown_attributes:
        attributed["unknown_attributes"] = sorted(unknown_attributes)
    if reserved_collisions:
        attributed["reserved_name_collisions"] = dict(sorted(reserved_collisions.items()))
    if attributed:
        doc["attributed"] = attributed

    quarantine = None
    s3_key = doc.get("s3_key")
    if isinstance(s3_key, str) and ORPHAN_MARKER in s3_key:
        doc["flags"] = ["orphaned_metadata"]
        quarantine = {
            "_id": item["id"],
            "tenant": ns,
            "reason": "orphaned_metadata",
            "s3_key": s3_key,
            "attribution": (
                "s3_key points at no seeded object in s3://otterworks-data-lake "
                "(planted anomaly kind=orphaned_metadata, "
                "contract docs/tech-partnerships/contracts/mongo_files.json)"
            ),
            "source": f"dynamodb.{DYNAMO_TABLE}",
        }
    return doc, quarantine


def ensure_indexes(files, quarantine) -> None:
    files.create_index([("tenant", 1), ("owner_id", 1)])
    files.create_index([("tenant", 1), ("s3_key", 1)])
    quarantine.create_index([("tenant", 1), ("reason", 1)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
    )
    args = parser.parse_args()
    if not NS_PATTERN.fullmatch(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    table = boto3.resource(
        "dynamodb",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    ).Table(DYNAMO_TABLE)

    client = MongoClient(args.mongo_uri)
    files = client[f"ow_tp_mongodb_{args.ns}"]["files"]
    quarantine = client[f"ow_tp_mongodb_{args.ns}_quarantine"]["files_quarantine"]

    migrated = 0
    quarantined = 0
    batches = 0
    scan_kwargs = {
        "FilterExpression": "#n = :ns",
        "ExpressionAttributeNames": {"#n": "ns"},
        "ExpressionAttributeValues": {":ns": args.ns},
    }
    while True:
        resp = table.scan(**scan_kwargs)
        items = resp.get("Items", [])
        if items:  # per-batch commit: one scan page = one bulk upsert
            file_ops, quarantine_ops = [], []
            for item in items:
                doc, q = transform(item, args.ns)
                file_ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
                if q is not None:
                    quarantine_ops.append(ReplaceOne({"_id": q["_id"]}, q, upsert=True))
            files.bulk_write(file_ops, ordered=False)
            if quarantine_ops:
                quarantine.bulk_write(quarantine_ops, ordered=False)
            migrated += len(file_ops)
            quarantined += len(quarantine_ops)
            batches += 1
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    if migrated == 0:
        log(f"ns={args.ns}: no source items — no-op, prior target output untouched")
        return 0

    ensure_indexes(files, quarantine)
    log(f"ns={args.ns}: migrated {migrated} items in {batches} batches, "
        f"quarantined {quarantined} orphaned_metadata")
    return 0


if __name__ == "__main__":
    sys.exit(main())

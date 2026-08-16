# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3", "pymongo"]
# ///
"""mongo_files unit: DynamoDB otterworks-file-metadata -> MongoDB `files`.

Contract: docs/tech-partnerships/contracts/mongo_files.json

Reads every item where ns=<ns> from the LocalStack DynamoDB table and upserts
one document per item (1:1) into ow_tp_mongodb_<ns>.files, keyed on the
deterministic source id. The `ns` attribute maps to a `tenant` field.

Policies (from the unit contract):
- byte transparency: string attribute values are carried verbatim; binary
  attributes become BSON binary; numbers become ints (DynamoDB numbers here
  are integral).
- malformed records: missing attributes are omitted, never fabricated; NULL
  attributes are omitted and attributed; unknown extra attributes are carried
  through under `extra_attributes` and attributed by name.
- items missing a critical identity attribute (id, ns, size_bytes, s3_key)
  never fail open into valid-looking documents: they are written only to the
  quarantine collection with attribution.
- orphaned metadata: an s3_key with the seeder's `/missing/` orphan marker
  (the same marker testdata/legacy/validate.py checks) points at no seeded
  object; the item is flagged in place and a quarantine record with full
  attribution is upserted.
- empty input: a namespace with no items writes nothing at all.
- trigger granularity: per-batch (one bulk upsert per DynamoDB scan page).

Idempotent by construction: upserts only, deterministic ids (source id for
files; uuid5 for quarantine records), no wall-clock values in any document.
"""

import argparse
import os
import re
import sys
import uuid
from decimal import Decimal

import boto3
from boto3.dynamodb.types import Binary
from bson.binary import Binary as BsonBinary
from pymongo import MongoClient, ReplaceOne

UNIT = "mongo_files"
DYNAMO_TABLE = "otterworks-file-metadata"
SOURCE = f"dynamodb.{DYNAMO_TABLE}"

# Deterministic namespace for quarantine-record ids (uuid5, never uuid4).
QUARANTINE_UUID_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "otterworks-tp-mongodb-quarantine")

CRITICAL_ATTRS = ("id", "ns", "size_bytes", "s3_key")
KNOWN_ATTRS = (
    "id", "ns", "name", "mime_type", "size_bytes", "s3_key", "folder_id",
    "owner_id", "version", "is_trashed", "created_at", "updated_at",
)
INT_ATTRS = {"size_bytes", "version"}
ORPHAN_MARKER = re.compile(r"(^|/)missing/")

NS_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def log(msg: str) -> None:
    print(f"[{UNIT}] {msg}", flush=True)


def to_bson(value):
    """Carry a DynamoDB attribute value into BSON without altering bytes."""
    if isinstance(value, Binary):
        return BsonBinary(bytes(value))
    if isinstance(value, bytes):
        return BsonBinary(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, list):
        return [to_bson(v) for v in value]
    if isinstance(value, set):
        return sorted(to_bson(v) for v in value)
    if isinstance(value, dict):
        return {k: to_bson(v) for k, v in value.items()}
    return value


def quarantine_id(ns: str, source_id: str, reason: str) -> str:
    return str(uuid.uuid5(QUARANTINE_UUID_NS, f"{UNIT}|{ns}|{source_id}|{reason}"))


def quarantine_record(ns: str, item: dict, reason: str, detail: str) -> ReplaceOne:
    source_id = str(item.get("id") or "")
    qid = quarantine_id(ns, source_id or repr(sorted(item)), reason)
    doc = {
        "_id": qid,
        "unit": UNIT,
        "tenant": ns,
        "source": SOURCE,
        "source_id": source_id or None,
        "reason": reason,
        "detail": detail,
        "source_item": {k: to_bson(v) for k, v in item.items()},
    }
    return ReplaceOne({"_id": qid}, doc, upsert=True)


def map_item(ns: str, item: dict) -> tuple[ReplaceOne | None, list[ReplaceOne]]:
    """Map one DynamoDB item to a files upsert plus any quarantine upserts."""
    quarantine: list[ReplaceOne] = []

    missing_critical = [
        a for a in CRITICAL_ATTRS if a not in item or item.get(a) is None
    ]
    if missing_critical:
        quarantine.append(quarantine_record(
            ns, item, "missing_critical_attribute",
            f"critical attribute(s) {missing_critical} missing or NULL"))
        return None, quarantine

    null_attrs = sorted(k for k, v in item.items() if v is None)
    extra_attrs = sorted(k for k in item if k not in KNOWN_ATTRS)

    doc: dict = {"_id": str(item["id"]), "tenant": str(item["ns"])}
    for attr in KNOWN_ATTRS:
        if attr in ("id", "ns"):
            continue
        if attr not in item or item[attr] is None:
            continue  # omitted, never fabricated
        value = to_bson(item[attr])
        if attr in INT_ATTRS and not isinstance(value, int):
            quarantine.append(quarantine_record(
                ns, item, "invalid_attribute",
                f"attribute {attr} is not integral: {value!r}"))
            return None, quarantine
        doc[attr] = value

    anomalies: list[str] = []
    if ORPHAN_MARKER.search(str(item["s3_key"])):
        anomalies.append("orphaned_s3_key")
        quarantine.append(quarantine_record(
            ns, item, "orphaned_s3_key",
            f"s3_key {item['s3_key']!r} points at no seeded object"))

    if extra_attrs:
        doc["extra_attributes"] = {a: to_bson(item[a]) for a in extra_attrs}

    attribution: dict = {"unit": UNIT, "source": SOURCE}
    if anomalies:
        attribution["anomalies"] = anomalies
    if null_attrs:
        attribution["null_attributes"] = null_attrs
    if extra_attrs:
        attribution["extra_attributes"] = extra_attrs
    doc["migration"] = attribution

    return ReplaceOne({"_id": doc["_id"]}, doc, upsert=True), quarantine


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--mongodb-uri",
                        default=os.getenv("MONGODB_URI"),
                        help="target MongoDB URI (local fixture in this phase; "
                             "the parent supplies MONGODB_ATLAS_URI live)")
    args = parser.parse_args()

    if not NS_PATTERN.fullmatch(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    if not args.mongodb_uri:
        print("--mongodb-uri (or MONGODB_URI) is required", file=sys.stderr)
        return 2

    ns = args.ns
    table = boto3.resource(
        "dynamodb",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    ).Table(DYNAMO_TABLE)

    client = MongoClient(args.mongodb_uri)
    files = client[f"ow_tp_mongodb_{ns}"]["files"]
    files_quarantine = client[f"ow_tp_mongodb_{ns}_quarantine"]["files_quarantine"]

    scan_kwargs = {
        "FilterExpression": "#n = :ns",
        "ExpressionAttributeNames": {"#n": "ns"},
        "ExpressionAttributeValues": {":ns": ns},
    }
    batches = upserted = quarantined = 0
    while True:
        resp = table.scan(**scan_kwargs)
        items = resp.get("Items", [])
        file_ops: list[ReplaceOne] = []
        quarantine_ops: list[ReplaceOne] = []
        for item in items:
            file_op, q_ops = map_item(ns, item)
            if file_op is not None:
                file_ops.append(file_op)
            quarantine_ops.extend(q_ops)
        # per-batch trigger granularity: one bulk upsert per scan page
        if file_ops:
            files.bulk_write(file_ops, ordered=False)
            upserted += len(file_ops)
        if quarantine_ops:
            files_quarantine.bulk_write(quarantine_ops, ordered=False)
            quarantined += len(quarantine_ops)
        if items:
            batches += 1
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    if batches == 0:
        log(f"ns={ns}: no source items — no-op, target left untouched")
    else:
        log(f"ns={ns}: {upserted} documents upserted into "
            f"ow_tp_mongodb_{ns}.files across {batches} batch(es), "
            f"{quarantined} quarantine record(s)")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

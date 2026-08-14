# /// script
# requires-python = ">=3.11"
# dependencies = ["boto3", "pymongo"]
# ///
"""
Migrate the seeded DynamoDB file metadata (otterworks-file-metadata, one
namespace slice selected by the `ns` attribute) into MongoDB Atlas.

Target: ow_tp_<ns>.files — one document per metadata item, 1:1. DynamoDB's
stringly-typed item becomes a typed document (Decimal -> int, ISO strings ->
BSON dates); the `ns` partition attribute becomes the tenant field. Items
whose s3_key carries the `<ns>/missing/…` marker (planted orphaned-metadata
anomalies) are migrated verbatim — reconciliation enumerates them, the
migration never drops them.

Streaming: paginated DynamoDB scan, bulk-written in batches; idempotent (the
run wipes and rebuilds only ow_tp_<ns>.files).

Usage:
    uv run migrations/mongodb/migrate_files.py --ns <ns>
"""

import argparse
import sys
import time
from datetime import datetime, timezone

from pymongo import InsertOne

from mongo_common import (
    BATCH,
    DYNAMO_TABLE,
    FILES_COLLECTION,
    aws_resource,
    db_name,
    log,
    mongo_client,
    valid_ns,
)


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def migrate(ns: str) -> None:
    client = mongo_client()
    coll = client[db_name(ns)][FILES_COLLECTION]
    coll.drop()

    table = aws_resource("dynamodb").Table(DYNAMO_TABLE)
    scan_kwargs = {
        "FilterExpression": "#n = :ns",
        "ExpressionAttributeNames": {"#n": "ns"},
        "ExpressionAttributeValues": {":ns": ns},
    }
    ops: list = []
    n_items = 0
    started = time.monotonic()
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            ops.append(InsertOne({
                "_id": item["id"],
                "tenant": item["ns"],
                "name": item["name"],
                "mime_type": item["mime_type"],
                "size_bytes": int(item["size_bytes"]),
                "s3_key": item["s3_key"],
                "folder_id": item["folder_id"],
                "owner_id": item["owner_id"],
                "version": int(item["version"]),
                "is_trashed": bool(item["is_trashed"]),
                "created_at": parse_ts(item["created_at"]),
                "updated_at": parse_ts(item["updated_at"]),
            }))
            n_items += 1
            if len(ops) >= BATCH:
                coll.bulk_write(ops, ordered=False)
                ops.clear()
        if "LastEvaluatedKey" not in resp:
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    if ops:
        coll.bulk_write(ops, ordered=False)

    coll.create_index("owner_id")
    log("migrate-files",
        f"ns={ns}: {n_items} metadata items -> {db_name(ns)}.{FILES_COLLECTION} "
        f"in {time.monotonic() - started:.1f}s")
    client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    args = parser.parse_args()
    if not valid_ns(args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    migrate(args.ns)
    return 0


if __name__ == "__main__":
    sys.exit(main())

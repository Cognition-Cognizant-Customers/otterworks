#!/usr/bin/env python3
"""Local S3 fixture for the storage_cleanup_daily baseline.

The legacy cron (`etl/scripts/storage_cleanup_daily.py`) names a bucket no local
fixture provides, so it cannot run as shipped:

    FATAL: An error occurred (NoSuchBucket) when calling the ListObjectsV2
    operation: The specified bucket does not exist

This builds that missing fixture in LocalStack from the already-seeded
DynamoDB metadata (`otterworks-file-metadata`, 10,000 items for ns=demo) so the
legacy script has something real to walk:

* one zero-byte object per metadata item whose `s3_key` is a live `<ns>/files/…`
  key -- the "live customer files" side, present in both stores;
* a planted orphan set: objects with **no** metadata row, keyed under the
  `files/` prefix the legacy script hardcodes (pre-namespace-era keys, the
  shape of a file whose metadata row was lost). Sizes and keys are derived from
  the namespace seed, so the set is identical on every rerun, and it is written
  out verbatim as the expected answer for reconciliation.

Nothing here touches the seeded stores: metadata items are read, never written.
The fixture is rebuilt from scratch on every run (objects under the two fixture
buckets are deleted first), so it is idempotent.

    fixture_storage_cleanup.py build --ns demo
    fixture_storage_cleanup.py show  --ns demo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
DYNAMO_TABLE = "otterworks-file-metadata"
FILE_STORAGE_BUCKET = "otterworks-file-storage"
QUARANTINE_BUCKET = "otterworks-file-quarantine"
LEGACY_PREFIX = "files/"  # the prefix the legacy script lists, hardcoded
PLANTED_ORPHANS = 25
GOLDEN_ROOT = Path(os.environ.get("TP_GOLDEN_ROOT", "/home/ubuntu/tp-golden")) / "python" / "storage_cleanup_daily"


def _client(service: str):
    return boto3.client(
        service,
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def ns_seed(ns: str) -> int:
    """Same namespace-derived seed the repo's generators use (testdata/legacy)."""
    return int(hashlib.sha256(ns.encode()).hexdigest()[:8], 16)


def scan_metadata(ns: str) -> list[dict]:
    """Every seeded metadata item for the namespace, read-only."""
    dynamodb = _client("dynamodb")
    items: list[dict] = []
    kwargs: dict = {
        "TableName": DYNAMO_TABLE,
        "ProjectionExpression": "id, s3_key, size_bytes, created_at, ns",
    }
    while True:
        page = dynamodb.scan(**kwargs)
        for raw in page.get("Items", []):
            if raw.get("ns", {}).get("S") != ns:
                continue
            items.append(
                {
                    "file_id": raw["id"]["S"],
                    "storage_key": raw["s3_key"]["S"],
                    "size_bytes": int(raw["size_bytes"]["N"]),
                    "created_at": raw["created_at"]["S"],
                }
            )
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def planted_orphans(ns: str) -> list[dict]:
    """The orphan set to plant: deterministic keys and sizes for the namespace.

    Keys sit directly under `files/` (no namespace segment) because that is the
    prefix the legacy script walks; each carries a real body of the recorded
    size so orphan_bytes is a measured sum, not a declared one.
    """
    rng = random.Random(ns_seed(ns) + 991)  # offset: a stream of its own, not the seeder's
    orphans = []
    for _ in range(PLANTED_ORPHANS):
        owner = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        obj = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        orphans.append(
            {
                "bucket": FILE_STORAGE_BUCKET,
                "key": f"{LEGACY_PREFIX}{owner}/{obj}",
                "size_bytes": rng.randrange(1024, 65536),
            }
        )
    orphans.sort(key=lambda o: o["key"])
    return orphans


def ensure_bucket(s3, bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)


def empty_prefix(s3, bucket: str, prefix: str) -> int:
    """Delete only what this fixture owns -- the buckets are shared per namespace."""
    deleted = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
    return deleted


def build(ns: str) -> dict:
    s3 = _client("s3")
    for bucket in (FILE_STORAGE_BUCKET, QUARANTINE_BUCKET):
        ensure_bucket(s3, bucket)
    # `<ns>/` covers the live keys and the quarantine bucket's namespaced copies;
    # the planted orphans are deleted by exact key, since they sit under the
    # un-namespaced `files/` prefix another namespace may also be using.
    removed = sum(
        empty_prefix(s3, bucket, f"{ns}/") for bucket in (FILE_STORAGE_BUCKET, QUARANTINE_BUCKET)
    )
    stale = [{"Key": o["key"]} for o in planted_orphans(ns)]
    for bucket in (FILE_STORAGE_BUCKET, QUARANTINE_BUCKET):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": stale})
        removed += len(stale)

    items = scan_metadata(ns)
    live_keys = sorted({i["storage_key"] for i in items if "/files/" in i["storage_key"]})
    marker_keys = sorted({i["storage_key"] for i in items if "/missing/" in i["storage_key"]})

    def put_live(key: str) -> None:
        # Stub bodies: the join is on keys, and materializing the metadata's
        # declared sizes would mean ~1 TB of LocalStack objects.
        s3.put_object(Bucket=FILE_STORAGE_BUCKET, Key=key, Body=b"")

    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(put_live, live_keys))

    orphans = planted_orphans(ns)
    for orphan in orphans:
        s3.put_object(
            Bucket=FILE_STORAGE_BUCKET,
            Key=orphan["key"],
            Body=b"\0" * orphan["size_bytes"],
        )

    manifest = {
        "ns": ns,
        "fixture": "storage_cleanup_daily",
        "endpoint": ENDPOINT,
        "buckets": {"file_storage": FILE_STORAGE_BUCKET, "quarantine": QUARANTINE_BUCKET},
        "objects_removed_before_build": removed,
        "metadata_items": len(items),
        "metadata_live_keys": len(live_keys),
        "metadata_missing_marker_keys": len(marker_keys),
        "live_objects_written": len(live_keys),
        "planted_orphan_count": len(orphans),
        "planted_orphan_bytes": sum(o["size_bytes"] for o in orphans),
        "objects_total": len(live_keys) + len(orphans),
        "legacy_visible_prefix": LEGACY_PREFIX,
        "legacy_visible_objects": len(orphans),
        "planted_orphans": orphans,
    }
    GOLDEN_ROOT.mkdir(parents=True, exist_ok=True)
    (GOLDEN_ROOT / "fixture_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def show(ns: str) -> dict:
    s3 = _client("s3")
    counts: dict[str, int] = {}
    sizes: dict[str, int] = {}
    for bucket in (FILE_STORAGE_BUCKET, QUARANTINE_BUCKET):
        counts[bucket] = 0
        sizes[bucket] = 0
        try:
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    counts[bucket] += 1
                    sizes[bucket] += obj["Size"]
        except ClientError:
            counts[bucket] = -1
    return {"ns": ns, "object_counts": counts, "object_bytes": sizes}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "show"])
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    args = parser.parse_args(argv)
    result = build(args.ns) if args.command == "build" else show(args.ns)
    summary = {k: v for k, v in result.items() if k != "planted_orphans"}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

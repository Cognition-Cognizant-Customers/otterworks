#!/usr/bin/env python3
"""Load the deterministic cron-cleanup estate into the live AWS target.

This is a parent-owned setup step, deliberately kept out of
``cron_cleanup_recon.py`` so that the recon itself stays read-only apart from
the probe it cleans up. Shapes and counts come from the immutable golden
baseline under ``testdata/legacy/golden/cronbox/``, never from the target.

    uv run --no-project --with boto3==1.35.99 python3 \
        scripts/tp_aws/seed_cron_cleanup_estate.py --ns demo

The metadata item is committed before its object so a referenced object is
never briefly unreferenced (the event-driven path resolves an object against
metadata the moment it lands). Orphans are written with no metadata item, and
the reverse orphan is a metadata item with no object.
"""

from __future__ import annotations

import argparse
import sys

import boto3
from cron_cleanup_recon import (
    METADATA_TABLE,
    REGION,
    STORAGE_BUCKET,
    expectations,
)


def seed_estate(s3, ddb, ns: str, exp: dict) -> None:
    for i, key in enumerate(exp["referenced_keys"]):
        ddb.put_item(
            TableName=METADATA_TABLE,
            Item={
                "id": {"S": f"file-{i:03d}"},
                "s3_key": {"S": key},
                "file_name": {"S": "Fichier \u0394 \u2615" if i == 7 else f"File {i}"},
                "owner_id": {"S": f"user-{i % 12:03d}"},
                "size_bytes": {"N": str(i + 10)},
            },
        )
        s3.put_object(Bucket=STORAGE_BUCKET, Key=key, Body=f"file-{i}-{ns}".encode())
    ddb.put_item(
        TableName=METADATA_TABLE,
        Item={
            "id": {"S": "reverse-orphan"},
            "s3_key": {"S": exp["reverse_orphan_key"]},
            "file_name": {"S": "reverse"},
            "owner_id": {"S": "user-000"},
            "size_bytes": {"N": "7"},
        },
    )
    for key in exp["orphan_keys"]:
        s3.put_object(Bucket=STORAGE_BUCKET, Key=key, Body=b"orphan")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default="demo")
    args = parser.parse_args()

    session = boto3.session.Session(region_name=REGION)
    exp = expectations(args.ns)
    seed_estate(session.client("s3"), session.client("dynamodb"), args.ns, exp)
    print(
        f"seeded {len(exp['referenced_keys'])} referenced object(s), "
        f"{len(exp['orphan_keys'])} object-only orphan(s), "
        f"{len(exp['reverse_orphan_ids'])} reverse metadata orphan(s) "
        f"into {STORAGE_BUCKET}/{METADATA_TABLE}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

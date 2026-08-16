#!/usr/bin/env python3
"""Extract stage for ow_tp_storage_cleanup: the two sides of the orphan join.

The legacy cron interleaved extract, comparison and a destructive quarantine in
one `main()`: it listed S3, scanned DynamoDB item by item, and deleted whatever
it could not find a metadata row for -- in the same pass, with no record of what
it read. Here extraction is a separate, re-runnable stage that lands two flat
extracts plus a manifest in the landing volume, and the manifest carries the one
fact the legacy script never recorded: **whether the metadata side was read
completely**. Everything downstream keys its safety decision off that.

    extract_storage_cleanup.py --ns demo
    extract_storage_cleanup.py --ns demo --metadata-limit 4000 \
        --input-dir storage_cleanup_partial   # simulated incomplete read

`--metadata-limit` truncates the metadata read deliberately (the transient
DynamoDB failure the legacy script could not distinguish from "these files are
orphans") and marks the manifest `metadata_read_complete=false`.

Local AWS is LocalStack (AWS_ENDPOINT_URL); the Databricks side is reached with
the shared driver, which reads DATABRICKS_HOST/TOKEN from the environment. No
credential is inlined here -- that is one of the deficiencies being retired.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx  # noqa: E402  (sibling module, same driver the recon uses)

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
DYNAMO_TABLE = "otterworks-file-metadata"
FILE_STORAGE_BUCKET = "otterworks-file-storage"
LEGACY_PREFIX = "files/"  # the un-namespaced prefix the legacy script hardcodes
OUT_ROOT = Path(os.environ.get("TP_EXTRACT_ROOT", "/tmp/ow_tp_extracts"))
NOTEBOOK = Path(__file__).resolve().parents[2] / "databricks" / "notebooks" / "storage_cleanup_daily.py"


def _load_notebook():
    spec = importlib.util.spec_from_file_location("ow_tp_storage_cleanup", NOTEBOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nb = _load_notebook()


def _validate_inputs(ns: str, scenario: str) -> None:
    nb._checked("ns", ns)
    nb._checked("scenario", scenario)


def _client(service: str):
    return boto3.client(
        service,
        endpoint_url=ENDPOINT,
        region_name=REGION,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def list_objects(bucket: str, ns: str) -> list[dict]:
    """Inventory of everything this namespace owns, under every prefix it uses.

    Two prefixes, no more: `<ns>/` (namespaced keys, the tenancy boundary in the
    shared bucket) and the un-namespaced `files/` the legacy script hardcodes.
    Wider than the legacy walk, which saw only `files/` and never reconciled
    anything stored elsewhere -- but never wider than the namespace, because an
    object listed here without a metadata row in *this* namespace is classified
    as an orphan, and a false positive is a deleted customer file.

    Keys under the legacy prefix predate namespacing, so nothing in the key
    attributes them to a tenant. Filtering keys claimed by another namespace
    happens after the metadata scan, once both sides of the inventory have
    been observed.
    """
    s3 = _client("s3")
    by_key: dict[str, dict] = {}
    for prefix in (f"{ns}/", LEGACY_PREFIX):
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                by_key[obj["Key"]] = {
                    "bucket": bucket,
                    "key": obj["Key"],
                    "size_bytes": obj["Size"],
                    "last_modified": obj["LastModified"].astimezone(timezone.utc).isoformat(),
                }
    return [by_key[key] for key in sorted(by_key)]


def filter_claimed_elsewhere(objects: list[dict], claimed_elsewhere: set) -> list[dict]:
    """Drop only legacy-prefix objects claimed by another namespace."""
    return [
        obj
        for obj in objects
        if not (obj["key"].startswith(LEGACY_PREFIX) and obj["key"] in claimed_elsewhere)
    ]


def scan_metadata(ns: str, limit: int | None = None) -> tuple[list[dict], bool, set]:
    """Metadata items for the namespace, and whether the read completed.

    Returns `(items, complete, claimed_elsewhere)`. `complete` is False when the
    in-namespace item list was truncated -- the distinction the legacy script
    structurally could not make. `claimed_elsewhere` holds the complete set of
    storage keys referenced by another namespace, even when `items` is
    truncated, which `filter_claimed_elsewhere` uses to keep another tenant's
    files out of this run's inventory.
    """
    dynamodb = _client("dynamodb")
    items: list[dict] = []
    claimed_elsewhere: set = set()
    truncated = False
    kwargs: dict = {
        "TableName": DYNAMO_TABLE,
        "ProjectionExpression": "id, s3_key, size_bytes, created_at, #n",
        "ExpressionAttributeNames": {"#n": "ns"},
    }
    while True:
        page = dynamodb.scan(**kwargs)
        for raw in page.get("Items", []):
            if raw.get("ns", {}).get("S") != ns:
                claimed_elsewhere.add(raw["s3_key"]["S"])
                continue
            if truncated:
                continue
            key = raw["s3_key"]["S"]
            items.append(
                {
                    "file_id": raw["id"]["S"],
                    "storage_key": key,
                    "owner_id": key.split("/")[2] if len(key.split("/")) > 2 else "",
                    "size_bytes": int(raw["size_bytes"]["N"]),
                    "created_at": raw["created_at"]["S"],
                }
            )
            if limit is not None and len(items) >= limit:
                truncated = True
        if "LastEvaluatedKey" not in page:
            if truncated:
                return items[:limit], False, claimed_elsewhere
            items.sort(key=lambda i: i["file_id"])
            return items, True, claimed_elsewhere
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _lit(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    # Spark honours backslash escapes inside string literals, so a key ending in
    # a backslash would otherwise escape its own closing quote: object keys are
    # user-supplied filenames, not trusted input.
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _ts(value: str) -> str:
    """Normalise an ISO instant to a UTC literal Spark casts unambiguously."""
    text = value.replace("Z", "+00:00")
    return datetime.fromisoformat(text).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _insert_rows(table: str, columns: str, rows: list[str], batch: int = 400) -> None:
    for start in range(0, len(rows), batch):
        values = ",".join(rows[start : start + batch])
        dbx.sql(f"INSERT INTO {table} ({columns}) VALUES {values}")


def load_bronze(ns: str, scenario: str, objects: list[dict], metadata: list[dict], manifest: dict) -> None:
    """Land both extracts in bronze through the serverless SQL warehouse.

    Bronze is loaded with the same statements the job task would run, keyed by
    `ns`: each slice is deleted before it is rewritten, so a re-run replaces its
    own rows instead of doubling the object count (and with it the orphan set).

    Why INSERT rather than the landing volume: the demo PAT is not granted the
    Files API (`files`) scope, so `dbx.upload` -- and therefore COPY INTO from
    /Volumes/ow_tp/bronze/landing -- returns 403 in this workspace. The SQL path
    needs no extra scope and lands identical rows.
    """
    for table in ("bronze.storage_objects_raw", "bronze.file_metadata_raw", "bronze.storage_extract_manifest"):
        dbx.sql(f"DELETE FROM ow_tp.{table} WHERE ns = {_lit(ns)}")

    listed_at = _ts(manifest["extracted_at"])
    _insert_rows(
        "ow_tp.bronze.storage_objects_raw",
        "ns, bucket, key, size_bytes, last_modified, listed_at",
        [
            "({}, {}, {}, {}, TIMESTAMP {}, TIMESTAMP {})".format(
                _lit(ns),
                _lit(o["bucket"]),
                _lit(o["key"]),
                _lit(o["size_bytes"]),
                _lit(_ts(o["last_modified"])),
                _lit(listed_at),
            )
            for o in objects
        ],
    )
    _insert_rows(
        "ow_tp.bronze.file_metadata_raw",
        "ns, file_id, storage_key, owner_id, size_bytes, created_at",
        [
            "({}, {}, {}, {}, {}, TIMESTAMP {})".format(
                _lit(ns),
                _lit(m["file_id"]),
                _lit(m["storage_key"]),
                _lit(m["owner_id"]),
                _lit(m["size_bytes"]),
                _lit(_ts(m["created_at"])),
            )
            for m in metadata
        ],
    )
    dbx.sql(
        "INSERT INTO ow_tp.bronze.storage_extract_manifest "
        "(ns, scenario, source_bucket, source_table, objects_expected, objects_bytes, "
        "metadata_expected, metadata_read_complete, extracted_at, loaded_at) VALUES "
        "({}, {}, {}, {}, {}, {}, {}, {}, TIMESTAMP {}, current_timestamp())".format(
            _lit(ns),
            _lit(scenario),
            _lit(manifest["source_bucket"]),
            _lit(manifest["source_table"]),
            _lit(manifest["objects_expected"]),
            _lit(manifest["objects_bytes"]),
            _lit(manifest["metadata_expected"]),
            _lit(manifest["metadata_read_complete"]),
            _lit(listed_at),
        )
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--bucket", default=FILE_STORAGE_BUCKET)
    parser.add_argument(
        "--input-dir",
        default="storage_cleanup",
        help="directory under <ns>/ in the landing volume to write the extract to",
    )
    parser.add_argument(
        "--metadata-limit",
        type=int,
        default=None,
        help="stop the metadata scan after N items and mark the read incomplete",
    )
    parser.add_argument(
        "--scenario",
        default="nominal",
        help="label recorded with the extract: nominal | metadata_read_incomplete",
    )
    parser.add_argument(
        "--load",
        action="store_true",
        help="also load the extract into ow_tp bronze via the serverless SQL warehouse",
    )
    args = parser.parse_args(argv)
    _validate_inputs(args.ns, args.scenario)

    # List first, then scan metadata: an object is visible to the join if its
    # metadata row lands before the second observation, avoiding a read-order
    # false orphan.
    objects = list_objects(args.bucket, args.ns)
    metadata, complete, claimed_elsewhere = scan_metadata(args.ns, args.metadata_limit)
    objects = filter_claimed_elsewhere(objects, claimed_elsewhere)

    out_dir = OUT_ROOT / args.ns / args.input_dir
    write_jsonl(out_dir / "objects.jsonl", objects)
    write_jsonl(out_dir / "metadata.jsonl", metadata)

    manifest = {
        "ns": args.ns,
        "unit": "storage_cleanup_daily",
        "extracted_at": datetime.now(tz=timezone.utc).isoformat(),
        "source_bucket": args.bucket,
        "source_table": DYNAMO_TABLE,
        "objects_expected": len(objects),
        "objects_bytes": sum(o["size_bytes"] for o in objects),
        # The guard's inputs: how many metadata rows this extract claims to
        # carry, and whether the scan that produced them ran to completion.
        "metadata_expected": len(metadata),
        "metadata_read_complete": complete,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")

    manifest["scenario"] = args.scenario
    if args.load:
        load_bronze(args.ns, args.scenario, objects, metadata, manifest)
        manifest["loaded"] = "ow_tp.bronze"

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Local runner for the converted `analytics_daily` pipeline.

Runs the *same* statement set as the `ow_tp_analytics_daily` job task: the statements are
imported from `databricks/notebooks/analytics_daily.py` (the job's notebook) and executed
on the pre-existing serverless SQL warehouse through `scripts/tp_databricks/dbx.py`. No
compute is created, and nothing is re-implemented here — that is what makes the recon
evidence produced this way evidence about the job.

Commands:
    pipeline_analytics_daily.py land  [--ns demo]  # legacy S3 event extract -> landing volume (+ DDL)
    pipeline_analytics_daily.py stage [--ns demo]  # same extract -> staging table, over SQL only
    pipeline_analytics_daily.py run   [--ns demo]  # DDL + bronze/silver/gold loads
    pipeline_analytics_daily.py counts [--ns demo] # bronze/silver/rejects/gold counts

`land` is the migration's ingest bridge: it copies the legacy event objects out of the
store the legacy cron read (LocalStack S3, `s3://otterworks-data-lake/events/<ns>/`) into
`/Volumes/<catalog>/bronze/landing/<ns>/analytics_daily/events/`, byte-for-byte, keeping
the `YYYY/MM/DD/HH.json.gz` layout so the load is auditable per source object.

`stage` is the fallback for a workspace token without the `files` scope, which the Files
API requires to write into a volume (`403 ... required scopes: files`). It loads the same
source lines, byte-for-byte and tagged with the S3 key they came from, into
`<catalog>.bronze.analytics_daily_stage` using only SQL, and `run --source-table` points
the extract at that table. Everything downstream is the same statement text, so the
transform is identical; only the transport differs, and a recon produced this way must say
so.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = REPO_ROOT / "databricks" / "notebooks" / "analytics_daily.py"
DDL_FILE = REPO_ROOT / "databricks" / "ddl" / "analytics_daily.sql"
STAGE_DDL_FILE = REPO_ROOT / "databricks" / "ddl" / "analytics_daily_stage.sql"
DATA_LAKE_BUCKET = "otterworks-data-lake"
STAGE_TABLE_SUFFIX = "bronze.analytics_daily_stage"
STAGE_BATCH_ROWS = 250


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dbx = _load(Path(__file__).with_name("dbx.py"), "tp_dbx")
pipeline = _load(NOTEBOOK, "tp_analytics_daily_notebook")


def volume_prefix(ns: str, catalog: str) -> str:
    return f"{ns}/analytics_daily"


def stage_table(catalog: str) -> str:
    return f"{catalog}.{STAGE_TABLE_SUFFIX}"


def _s3_client():
    import boto3  # local-fixture only dependency; the job never talks to AWS

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _event_objects(ns: str) -> tuple[str, list[str]]:
    s3, prefix, keys = _s3_client(), f"events/{ns}/", []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=DATA_LAKE_BUCKET, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    if not keys:
        raise SystemExit(f"no event objects under s3://{DATA_LAKE_BUCKET}/{prefix}; run `make seed-legacy NS={ns}` first")
    return prefix, sorted(keys)


def land(ns: str, catalog: str) -> dict:
    """Copy the legacy event objects and the DDL into the landing volume."""
    s3 = _s3_client()
    prefix, keys = _event_objects(ns)

    landed, total_bytes = 0, 0
    with tempfile.TemporaryDirectory() as scratch:
        for key in keys:
            body = s3.get_object(Bucket=DATA_LAKE_BUCKET, Key=key)["Body"].read()
            local = Path(scratch) / Path(key).name
            local.write_bytes(body)
            relative = key[len(prefix):]  # YYYY/MM/DD/HH.json.gz
            dbx.upload(str(local), f"{volume_prefix(ns, catalog)}/events/{relative}")
            landed += 1
            total_bytes += len(body)
    ddl_target = dbx.upload(str(DDL_FILE), f"{volume_prefix(ns, catalog)}/ddl/analytics_daily.sql")
    return {"objects": landed, "bytes": total_bytes, "ddl": ddl_target}


def stage(ns: str, catalog: str) -> dict:
    """Load the legacy event lines into the staging table using SQL only.

    Lines are transported as base64 and decoded in SQL (`unbase64`), so no quoting or
    escaping can alter a byte of the source record.
    """
    import base64
    import gzip

    s3 = _s3_client()
    prefix, keys = _event_objects(ns)
    table = stage_table(catalog)

    for statement in pipeline.ddl_statements(STAGE_DDL_FILE.read_text(encoding="utf-8"), catalog):
        dbx.sql(statement["sql"])
    dbx.sql(f"DELETE FROM {table} WHERE ns = '{ns}'")

    staged, batch = 0, []
    for key in keys:
        body = s3.get_object(Bucket=DATA_LAKE_BUCKET, Key=key)["Body"].read()
        payload = gzip.decompress(body) if key.endswith(".gz") else body
        source_object = key[len(prefix):]
        for line in payload.decode("utf-8").splitlines():
            if not line.strip():
                continue
            encoded = base64.b64encode(line.encode("utf-8")).decode("ascii")
            batch.append(f"('{ns}', '{source_object}', CAST(unbase64('{encoded}') AS STRING), current_timestamp())")
            if len(batch) >= STAGE_BATCH_ROWS:
                dbx.sql(f"INSERT INTO {table} VALUES {', '.join(batch)}")
                staged += len(batch)
                batch = []
    if batch:
        dbx.sql(f"INSERT INTO {table} VALUES {', '.join(batch)}")
        staged += len(batch)

    in_table = int(dbx.sql_scalar(f"SELECT count(*) FROM {table} WHERE ns = '{ns}'"))
    if in_table != staged:
        raise SystemExit(f"staged {staged} lines but {table} holds {in_table} for ns={ns}")
    return {"objects": len(keys), "lines": staged, "table": table}


def run(ns: str, catalog: str, source_kind: str, apply_ddl: bool = True, source_table: str | None = None) -> dict:
    return pipeline.run_pipeline(
        execute=dbx.sql,
        scalar=dbx.sql_scalar,
        ddl_text=DDL_FILE.read_text(encoding="utf-8"),
        catalog=catalog,
        ns=ns,
        source_kind=source_kind,
        source_table=source_table,
        apply_ddl=apply_ddl,
    )


def counts(ns: str, catalog: str) -> dict[str, int]:
    return {key: int(dbx.sql_scalar(query)) for key, query in pipeline.count_queries(catalog, ns).items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("land", "stage", "run", "counts"))
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--catalog", default=pipeline.DEFAULT_CATALOG)
    parser.add_argument("--source-kind", default=pipeline.DEFAULT_SOURCE_KIND, choices=("s3", "sqs", "dynamodb"))
    parser.add_argument("--skip-ddl", action="store_true")
    parser.add_argument(
        "--source-table",
        nargs="?",
        const="",
        default=None,
        help="read the extract from a staging table instead of the landing volume; "
        "bare flag means <catalog>.bronze.analytics_daily_stage",
    )
    args = parser.parse_args(argv)

    if args.command == "land":
        result = land(args.ns, args.catalog)
    elif args.command == "stage":
        result = stage(args.ns, args.catalog)
    elif args.command == "run":
        source_table = stage_table(args.catalog) if args.source_table == "" else args.source_table
        result = run(args.ns, args.catalog, args.source_kind, apply_ddl=not args.skip_ddl, source_table=source_table)
    else:
        result = counts(args.ns, args.catalog)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Land the user-activity job's inputs into Unity Catalog.

The legacy job read two things: per-user event objects from S3
(`events/<ns>/YYYY/MM/DD/HH.json.gz`) and the upstream aggregate table
`analytics_daily_summary` in PostgreSQL. This script is the ingest edge of the
conversion: it copies both into the shared demo estate so the notebook can read
them set-based, with no credentials of its own.

  events   -> /Volumes/<catalog>/bronze/landing/<ns>/user_activity/events/date=YYYY-MM-DD/events.json
  DDL      -> /Volumes/<catalog>/bronze/landing/<ns>/user_activity/ddl/*.sql
  upstream -> <catalog>.bronze.user_activity_upstream_fixture

Credentials: Databricks from DATABRICKS_HOST/DATABRICKS_TOKEN (or the _DEMO_
variants) via dbx.py; S3 and PostgreSQL from the environment / the scratch
`config.ini` used to run the legacy job. Nothing is hardcoded, and only
`ow_tp`-prefixed objects and the namespace's own prefixes are touched.

Usage:
    land_user_activity.py --ns demo [--upstream-through YYYY-MM-DD] [--no-upstream]

`--upstream-through` lands only upstream rows up to that date, which is how the
freshness guard is exercised against a late upstream; `--no-upstream` empties the
fixture entirely, which is how it is exercised against an upstream that never ran.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import tempfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dbx  # noqa: E402
from pipeline_module import load_pipeline  # noqa: E402

pipeline = load_pipeline()

DEFAULT_BUCKET = os.environ.get("OW_DATA_LAKE_BUCKET", "otterworks-data-lake")
DDL_FILES = ("user_activity_tables.sql", "user_activity_landing.sql")
UPSTREAM_COLUMNS = [
    "active_users",
    "active_documents",
    "active_files",
    "total_events",
    "documents_created",
    "documents_edited",
    "comments_added",
    "files_uploaded",
    "files_shared",
    "files_deleted",
    "bytes_uploaded",
]


def s3_client():
    import boto3

    endpoint = os.environ.get("AWS_ENDPOINT_URL") or os.environ.get("S3_ENDPOINT_URL")
    return boto3.client("s3", endpoint_url=endpoint)


def read_events(ns: str, bucket: str) -> dict[str, list[dict]]:
    """Read every seeded event object for the namespace, grouped by event date."""
    client = s3_client()
    per_date: dict[str, list[dict]] = defaultdict(list)
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"events/{ns}/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json.gz"):
                continue
            body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            for line in gzip.GzipFile(fileobj=io.BytesIO(body)).read().decode().splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                per_date[event["occurred_at"][:10]].append(event)
    return per_date


def land_events_to_volume(ns: str, per_date: dict[str, list[dict]]) -> int:
    total = 0
    with tempfile.TemporaryDirectory() as tmp:
        for date, events in sorted(per_date.items()):
            local = os.path.join(tmp, f"{date}.json")
            with open(local, "w", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event, sort_keys=True) + "\n")
            target = dbx.upload(local, f"{ns}/user_activity/events/date={date}/events.json")
            print(f"  landed {len(events):5d} events -> {target}")
            total += len(events)
    return total


def quote(value: object) -> str:
    """A SQL string literal for an arbitrary value read out of the data lake.

    The event objects are seeded fixtures, but they are still untrusted input to the
    statements below: a quote or backslash in any field would otherwise close the
    literal and let the object's contents run as SQL under the runner's identity.
    """
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def land_events_to_table(ns: str, catalog: str, per_date: dict[str, list[dict]],
                        batch: int = 400) -> int:
    """Fallback ingest for a token without the `files` scope: land into a Delta table."""
    table = f"{catalog}.bronze.user_activity_events_landed"
    dbx.sql(f"DELETE FROM {table} WHERE ns = {quote(ns)}")
    flat = [event for _, events in sorted(per_date.items()) for event in events]
    for start in range(0, len(flat), batch):
        values = ",\n".join(
            "({ns}, {event_id}, {event_type}, {user_id}, {resource_id}, "
            "TIMESTAMP{occurred_at}, CURRENT_TIMESTAMP())".format(
                ns=quote(ns),
                event_id=quote(event["event_id"]),
                event_type=quote(event["event_type"]),
                user_id=quote(event["user_id"]),
                resource_id=quote(event.get("resource_id") or ""),
                occurred_at=quote(event["occurred_at"].replace("T", " ").replace("Z", "")),
            )
            for event in flat[start:start + batch]
        )
        dbx.sql(f"INSERT INTO {table} VALUES\n{values}")
        print(f"  landed events {start + 1}-{min(start + batch, len(flat))} -> {table}")
    return len(flat)


def missing_files_scope(exc: dbx.DatabricksError) -> bool:
    """True only for the token-scope refusal, not for any other upload failure.

    The bare word `files` is useless as a discriminator: every Files API path contains
    it, so it appears in the message of a 404 or a 500 too. The scope refusal is a 403
    whose body names the missing scope.
    """
    return exc.status == 403 and "required scopes" in str(exc) and "files" in str(exc).split(
        "required scopes", 1)[1]


def land_events(ns: str, catalog: str, bucket: str, mode: str) -> tuple[int, int, str]:
    per_date = read_events(ns, bucket)
    if mode in ("auto", "volume"):
        try:
            return len(per_date), land_events_to_volume(ns, per_date), "volume"
        except dbx.DatabricksError as exc:
            if mode == "volume" or not missing_files_scope(exc):
                raise
            print(f"  volume upload unavailable ({exc.status}: token lacks the `files` scope);"
                  " falling back to table ingest")
    return len(per_date), land_events_to_table(ns, catalog, per_date), "table"


def land_ddl(ns: str) -> None:
    ddl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "databricks", "ddl")
    for name in DDL_FILES:
        try:
            target = dbx.upload(os.path.join(ddl_dir, name), f"{ns}/user_activity/ddl/{name}")
        except dbx.DatabricksError as exc:
            if not missing_files_scope(exc):
                raise
            print("  DDL not landed on the volume (token lacks the `files` scope);"
                  " applied from the repo instead")
            return
        print(f"  landed DDL -> {target}")


def apply_ddl(catalog: str) -> None:
    ddl_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "databricks", "ddl")
    for name in DDL_FILES:
        with open(os.path.join(ddl_dir, name), encoding="utf-8") as handle:
            body = handle.read()
        for statement in pipeline.ddl_statements(body):
            dbx.sql(statement, catalog=catalog)
    print(f"  applied DDL in catalog {catalog}")


def upstream_rows(ns: str, through: str | None) -> list[dict]:
    """Read the upstream aggregate rows the legacy job consumed, from PostgreSQL."""
    import psycopg2

    dsn = {
        "host": os.environ.get("DB_HOST", "localhost"),
        # The seeded fixture instance, not host 5432: that port is occupied by an
        # unrelated Postgres that rejects these credentials, and it is where
        # fixture_analytics_upstream.py writes analytics_daily_summary.
        "port": int(os.environ.get("DB_PORT", "55432")),
        "dbname": os.environ.get("DB_NAME", "otterworks"),
        "user": os.environ.get("DB_USER", "otterworks"),
        "password": os.environ.get("DB_PASSWORD", ""),
    }
    query = f"SELECT report_date, {', '.join(UPSTREAM_COLUMNS)} FROM analytics_daily_summary"
    params: list = []
    if through:
        query += " WHERE report_date <= %s"
        params.append(through)
    query += " ORDER BY report_date"
    with psycopg2.connect(**dsn) as conn, conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    return [dict(zip(["report_date"] + UPSTREAM_COLUMNS, row)) for row in rows]


def land_upstream(ns: str, catalog: str, through: str | None, skip: bool) -> int:
    table = f"{catalog}.bronze.user_activity_upstream_fixture"
    dbx.sql(f"DELETE FROM {table} WHERE ns = {quote(ns)}")
    if skip:
        print(f"  upstream fixture emptied for ns={ns} (simulating an upstream that never ran)")
        return 0
    rows = upstream_rows(ns, through)
    if not rows:
        print(f"  no upstream rows available for ns={ns}")
        return 0
    values = ",\n".join(
        "({ns}, DATE{date}, {counts}, CURRENT_TIMESTAMP())".format(
            ns=quote(ns),
            date=quote(row["report_date"]),
            counts=", ".join(str(int(row[col] or 0)) for col in UPSTREAM_COLUMNS),
        )
        for row in rows
    )
    dbx.sql(f"INSERT INTO {table} VALUES\n{values}")
    print(f"  landed {len(rows)} upstream aggregate rows -> {table}"
          + (f" (through {through})" if through else ""))
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--catalog", default=dbx.CATALOG)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--upstream-through", default=None,
                        help="land upstream rows only up to this date (late-upstream scenario)")
    parser.add_argument("--no-upstream", action="store_true",
                        help="land no upstream rows at all (upstream-never-ran scenario)")
    parser.add_argument("--events-only", action="store_true",
                        help="land the events and leave the upstream fixture untouched")
    parser.add_argument("--upstream-only", action="store_true",
                        help="re-land only the upstream fixture (the freshness scenarios, "
                             "without re-uploading every event)")
    parser.add_argument("--mode", choices=("auto", "volume", "table"), default="auto",
                        help="event ingest target; auto falls back to the table when the "
                             "workspace token has no `files` scope")
    args = parser.parse_args(argv)
    if args.events_only and args.upstream_only:
        parser.error("--events-only and --upstream-only are mutually exclusive")
    if args.catalog != dbx.CATALOG:
        # dbx.upload() derives the volume path from its own module-level catalog, so a
        # divergent --catalog would put the files in one catalog and the tables in another.
        parser.error(f"--catalog {args.catalog} does not match the driver's catalog "
                     f"{dbx.CATALOG}; set OW_TP_CATALOG so files and tables agree")

    print(f"landing user_activity inputs for ns={args.ns} into {args.catalog}")
    land_ddl(args.ns)
    apply_ddl(args.catalog)
    if not args.upstream_only:
        dates, events, mode = land_events(args.ns, args.catalog, args.bucket, args.mode)
        print(f"  {events} events across {dates} dates (source_mode={mode})")
    if not args.events_only:
        land_upstream(args.ns, args.catalog, args.upstream_through, args.no_upstream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

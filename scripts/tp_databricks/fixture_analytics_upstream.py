#!/usr/bin/env python3
"""Build the upstream analytics fixture the legacy user-activity cron consumes.

`etl/scripts/user_activity_daily.py` reads two artifacts that only exist once
`etl/scripts/analytics_daily.py` has run: the PostgreSQL table
`analytics_daily_summary` and the per-user daily files
`s3://<data-lake>/analytics/daily/year=YYYY/month=MM/day=DD/top_users.jsonl.gz`.
The analytics cron cannot run locally (its SQS queue and the DynamoDB table
`otterworks-analytics-events` do not exist), so this script rebuilds those two
artifacts from the seeded event objects under
`s3://otterworks-data-lake/events/<ns>/` — the same source data the analytics
cron would have consumed.

It is a FIXTURE BUILDER, not a golden baseline: the golden baseline is the output
of the unmodified legacy script run against these artifacts. The mapping rules
below are the fixture's definition and are quoted verbatim in the recon report.

Aggregation rules, per event date (`occurred_at[:10]`):

* per-user records keep `analytics_daily.py`'s shape and truncation —
  `{"user_id", "actions": {<event_type>: count}, "total"}`, sorted by `total`
  descending, top 100 users per date; action-type keys are the seeded event
  types verbatim (`document.created`, ...) rather than the camel/underscore
  vocabulary the analytics cron expected from DynamoDB, because the seeded
  events are the only per-user source available.
* summary columns use `analytics_daily.py`'s own definitions with the seeded
  event vocabulary substituted:
  `documents_created`=`document.created`, `documents_edited`=`document.updated`,
  `comments_added`=`comment.added` (none seeded -> 0),
  `files_uploaded`=`file.uploaded`, `files_shared`=`file.shared`
  (none seeded -> 0), `files_deleted`=`file.trashed`,
  `bytes_uploaded`=0 (seeded events carry no `sizeBytes`),
  `active_users`=distinct `user_id`, `active_documents`=distinct `resource_id`
  over document create/update, `active_files`=distinct `resource_id` over file
  upload/share/delete, `total_events`=events on that date.

Usage:
    fixture_analytics_upstream.py --ns demo [--db-port 55432] [--out DIR]
"""

from __future__ import annotations

import argparse
import collections
import gzip
import io
import json
import os
import sys

import boto3
import psycopg2

DATA_LAKE_BUCKET = "otterworks-data-lake"
ANALYTICS_PREFIX = "analytics/daily"
TOP_USERS_PER_DATE = 100  # analytics_daily.py truncates to the top 100

DOC_CREATED = "document.created"
DOC_UPDATED = "document.updated"
COMMENT_ADDED = "comment.added"
FILE_UPLOADED = "file.uploaded"
FILE_SHARED = "file.shared"
FILE_DELETED = "file.trashed"
DOC_RESOURCE_EVENTS = (DOC_CREATED, DOC_UPDATED)
FILE_RESOURCE_EVENTS = (FILE_UPLOADED, FILE_SHARED, FILE_DELETED)

SUMMARY_DDL = """
    CREATE TABLE IF NOT EXISTS analytics_daily_summary (
        report_date       date PRIMARY KEY,
        active_users      integer NOT NULL,
        active_documents  integer NOT NULL,
        active_files      integer NOT NULL,
        total_events      integer NOT NULL,
        documents_created integer NOT NULL,
        documents_edited  integer NOT NULL,
        comments_added    integer NOT NULL,
        files_uploaded    integer NOT NULL,
        files_shared      integer NOT NULL,
        files_deleted     integer NOT NULL,
        bytes_uploaded    bigint  NOT NULL,
        updated_at        timestamptz NOT NULL DEFAULT NOW()
    );
"""

UPSERT_SQL = """
    INSERT INTO analytics_daily_summary (
        report_date, active_users, active_documents, active_files,
        total_events, documents_created, documents_edited, comments_added,
        files_uploaded, files_shared, files_deleted, bytes_uploaded, updated_at
    ) VALUES (%(report_date)s, %(active_users)s, %(active_documents)s,
        %(active_files)s, %(total_events)s, %(documents_created)s,
        %(documents_edited)s, %(comments_added)s, %(files_uploaded)s,
        %(files_shared)s, %(files_deleted)s, %(bytes_uploaded)s, NOW())
    ON CONFLICT (report_date) DO UPDATE SET
        active_users = EXCLUDED.active_users,
        active_documents = EXCLUDED.active_documents,
        active_files = EXCLUDED.active_files,
        total_events = EXCLUDED.total_events,
        documents_created = EXCLUDED.documents_created,
        documents_edited = EXCLUDED.documents_edited,
        comments_added = EXCLUDED.comments_added,
        files_uploaded = EXCLUDED.files_uploaded,
        files_shared = EXCLUDED.files_shared,
        files_deleted = EXCLUDED.files_deleted,
        bytes_uploaded = EXCLUDED.bytes_uploaded,
        updated_at = NOW();
"""


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("LOCALSTACK_ACCESS_KEY", "test"),
        aws_secret_access_key=os.getenv("LOCALSTACK_SECRET_KEY", "test"),
    )


def read_events(client, ns: str) -> list[dict]:
    events = []
    pages = client.get_paginator("list_objects_v2").paginate(
        Bucket=DATA_LAKE_BUCKET, Prefix=f"events/{ns}/"
    )
    for page in pages:
        for obj in page.get("Contents", []):
            body = client.get_object(Bucket=DATA_LAKE_BUCKET, Key=obj["Key"])["Body"].read()
            for line in gzip.decompress(body).decode("utf-8").strip().split("\n"):
                if line:
                    events.append(json.loads(line))
    return events


def aggregate(events: list[dict]) -> dict[str, dict]:
    """Per event date: the summary row and the per-user records."""
    by_date: dict[str, list[dict]] = collections.defaultdict(list)
    for event in events:
        by_date[event["occurred_at"][:10]].append(event)

    result = {}
    for date, day_events in sorted(by_date.items()):
        types = collections.Counter(e["event_type"] for e in day_events)
        per_user: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for event in day_events:
            per_user[event["user_id"]][event["event_type"]] += 1

        users = [
            {"user_id": uid, "actions": dict(actions), "total": sum(actions.values())}
            for uid, actions in per_user.items()
        ]
        users.sort(key=lambda u: (-u["total"], u["user_id"]))

        result[date] = {
            "summary": {
                "report_date": date,
                "active_users": len(per_user),
                "active_documents": len({
                    e["resource_id"] for e in day_events
                    if e["event_type"] in DOC_RESOURCE_EVENTS
                }),
                "active_files": len({
                    e["resource_id"] for e in day_events
                    if e["event_type"] in FILE_RESOURCE_EVENTS
                }),
                "total_events": len(day_events),
                "documents_created": types[DOC_CREATED],
                "documents_edited": types[DOC_UPDATED],
                "comments_added": types[COMMENT_ADDED],
                "files_uploaded": types[FILE_UPLOADED],
                "files_shared": types[FILE_SHARED],
                "files_deleted": types[FILE_DELETED],
                "bytes_uploaded": 0,
            },
            "users": users[:TOP_USERS_PER_DATE],
            "users_untruncated": len(users),
        }
    return result


def write_top_users(client, date: str, users: list[dict]) -> str:
    key = f"{ANALYTICS_PREFIX}/year={date[:4]}/month={date[5:7]}/day={date[8:10]}/top_users.jsonl.gz"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        for user in users:
            gz.write(json.dumps(user).encode("utf-8"))
            gz.write(b"\n")
    client.put_object(Bucket=DATA_LAKE_BUCKET, Key=key, Body=buf.getvalue())
    return key


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--db-host", default=os.getenv("DB_HOST", "localhost"))
    parser.add_argument("--db-port", type=int, default=int(os.getenv("DB_PORT", "55432")))
    parser.add_argument("--db-name", default=os.getenv("DB_NAME", "otterworks"))
    parser.add_argument("--db-user", default=os.getenv("DB_USER", "otterworks"))
    parser.add_argument("--db-password", default=os.getenv("DB_PASSWORD", "otterworks_dev"))
    parser.add_argument("--out", help="also write the aggregate as JSON here")
    args = parser.parse_args(argv)

    client = s3_client()
    events = read_events(client, args.ns)
    if not events:
        print(f"no seeded events under s3://{DATA_LAKE_BUCKET}/events/{args.ns}/", file=sys.stderr)
        return 1
    aggregates = aggregate(events)
    print(f"[fixture] {len(events)} seeded events over {len(aggregates)} dates")

    for date, payload in aggregates.items():
        key = write_top_users(client, date, payload["users"])
        print(
            f"[fixture] {date}: {payload['summary']['total_events']} events, "
            f"{payload['users_untruncated']} users -> s3://{DATA_LAKE_BUCKET}/{key}"
        )

    conn = psycopg2.connect(
        host=args.db_host, port=args.db_port, dbname=args.db_name,
        user=args.db_user, password=args.db_password,
    )
    try:
        with conn, conn.cursor() as cursor:
            cursor.execute(SUMMARY_DDL)
            for payload in aggregates.values():
                cursor.execute(UPSERT_SQL, payload["summary"])
    finally:
        conn.close()
    print(f"[fixture] upserted {len(aggregates)} rows into analytics_daily_summary")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(aggregates, handle, indent=2, sort_keys=True)
        print(f"[fixture] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

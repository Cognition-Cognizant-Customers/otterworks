#!/usr/bin/env python3
"""Age the seeded event stream into audit events past the 90-day retention horizon.

`etl/scripts/audit_archive_weekly.py` scans the DynamoDB table
`otterworks-audit-events` for events older than 90 days. The local estate seeds
three days of events (`s3://otterworks-data-lake/events/<ns>/`, anchor
2026-08-01T00:00:00Z) and leaves the audit table empty, so the legacy run exits 0
having archived nothing -- a vacuous baseline. This projects the seeded events
into audit events with timestamps aged past the cutoff, so the legacy job
produces a real archive artifact to reconcile against.

Deterministic by construction, from the seeded namespace only:

    age_days  = 30 + (sha256(event_id) % 300)
    timestamp = occurred_at - age_days days      (2026-08-01 anchor - 30..333d)

plus three boundary probes: the three lexicographically smallest `event_id`s are
pinned to cutoff-1s / cutoff / cutoff+1s, where cutoff = run_date - 90 days. The
legacy scan filter is `timestamp < :cutoff` (a string compare on the same
`%Y-%m-%dT%H:%M:%SZ` format), so the cutoff is exclusive and only the cutoff-1s
probe may be archived.

The same records are written to the DynamoDB table (legacy input) and to a JSONL
file (lakehouse bronze input), so both sides of the reconciliation read one
source fixture.

    seed_audit_events.py --ns demo --run-date 2026-08-01 --out audit_events.jsonl
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import boto3

DATA_LAKE_BUCKET = "otterworks-data-lake"
AUDIT_TABLE = "otterworks-audit-events"
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
RETENTION_DAYS = 90
AGE_MIN_DAYS = 30
AGE_SPREAD_DAYS = 300


def aws(service: str):
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    return boto3.client(service, endpoint_url=endpoint) if endpoint else boto3.client(service)


def cutoff_ts(run_date: str) -> str:
    return (datetime.strptime(run_date, "%Y-%m-%d") - timedelta(days=RETENTION_DAYS)).strftime(TS_FORMAT)


def read_seeded_events(ns: str) -> list[dict]:
    """Every event object seeded under events/<ns>/, in key order."""
    s3 = aws("s3")
    events: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=DATA_LAKE_BUCKET, Prefix=f"events/{ns}/"):
        for obj in sorted(page.get("Contents", []), key=lambda o: o["Key"]):
            body = s3.get_object(Bucket=DATA_LAKE_BUCKET, Key=obj["Key"])["Body"].read()
            for line in gzip.decompress(body).decode().splitlines():
                if line.strip():
                    events.append(json.loads(line))
    return events


def age(event: dict) -> str:
    digest = int(hashlib.sha256(event["event_id"].encode()).hexdigest(), 16)
    age_days = AGE_MIN_DAYS + digest % AGE_SPREAD_DAYS
    occurred = datetime.strptime(event["occurred_at"], TS_FORMAT).replace(tzinfo=timezone.utc)
    return (occurred - timedelta(days=age_days)).strftime(TS_FORMAT)


def build_records(ns: str, run_date: str) -> tuple[list[dict], dict[str, str]]:
    """Aged audit events, plus the boundary-probe event_id -> timestamp mapping."""
    events = read_seeded_events(ns)
    by_id = {e["event_id"]: e for e in events}
    cutoff = datetime.strptime(cutoff_ts(run_date), TS_FORMAT).replace(tzinfo=timezone.utc)
    probes = {
        event_id: (cutoff + timedelta(seconds=offset)).strftime(TS_FORMAT)
        for event_id, offset in zip(sorted(by_id), (-1, 0, 1))
    }

    records = []
    for event_id in sorted(by_id):
        event = by_id[event_id]
        records.append({
            "ns": ns,
            "id": event_id,  # the local table's hash key
            "event_id": event_id,
            "timestamp": probes.get(event_id, age(event)),
            "actor": event["user_id"],
            "action": event["event_type"],
            "target_id": event["resource_id"],
            "raw_payload": json.dumps(event, sort_keys=True),
            "ingested_at": event["occurred_at"],
        })
    return records, probes


def load_dynamodb(records: list[dict]) -> None:
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    resource = boto3.resource("dynamodb", endpoint_url=endpoint) if endpoint else boto3.resource("dynamodb")
    table = resource.Table(AUDIT_TABLE)
    with table.batch_writer(overwrite_by_pkeys=["id"]) as writer:
        for record in records:
            writer.put_item(Item=record)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--run-date", required=True, help="legacy execution date, UTC (cutoff = run_date - 90d)")
    parser.add_argument("--out", required=True, help="JSONL fixture written for the lakehouse bronze load")
    parser.add_argument("--skip-dynamodb", action="store_true")
    args = parser.parse_args()

    records, probes = build_records(args.ns, args.run_date)
    cutoff = cutoff_ts(args.run_date)
    expected = [r["event_id"] for r in records if r["timestamp"] < cutoff]

    with open(args.out, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    if not args.skip_dynamodb:
        load_dynamodb(records)

    with open(args.out, "rb") as handle:
        fixture_sha256 = hashlib.sha256(handle.read()).hexdigest()

    print(json.dumps({
        "ns": args.ns,
        "run_date": args.run_date,
        "cutoff_ts": cutoff,
        "records": len(records),
        "past_cutoff": len(expected),
        "boundary_probes": probes,
        "fixture": args.out,
        "fixture_sha256": fixture_sha256,
        "event_id_set_sha256": hashlib.sha256("\n".join(sorted(expected)).encode()).hexdigest(),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

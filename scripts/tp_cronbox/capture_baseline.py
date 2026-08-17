#!/usr/bin/env python3
"""Capture a deterministic, store-derived snapshot after one Cron Box job."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import psycopg2
import requests
from common import (
    GOLDEN,
    clients,
    dynamo_scan_all,
    pg_kwargs,
    s3_objects_all,
    write_json,
)

MAX_ARTIFACT_BYTES = 5 * 1024 * 1024


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", required=True)
    parser.add_argument("--run-date", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    s3, dynamo, _ = clients()
    out = GOLDEN / args.ns / args.job
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "namespace": args.ns,
        "run_date": args.run_date,
        "job": args.job,
        "s3": {},
        "sha256": {},
        "dynamodb": {},
        "postgres": {},
        "meilisearch": {},
    }
    for bucket in (
        "otterworks-data-lake",
        "otterworks-file-quarantine",
        "otterworks-audit-archive",
    ):
        keys = sorted(x["Key"] for x in s3_objects_all(s3, bucket))
        manifest["s3"][bucket] = keys
        for key in keys:
            try:
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            except s3.exceptions.InvalidObjectState:
                s3.restore_object(Bucket=bucket, Key=key, RestoreRequest={"Days": 1})
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            digest_body = gzip.decompress(body) if key.endswith(".gz") else body
            manifest["sha256"][f"{bucket}/{key}"] = hashlib.sha256(
                digest_body
            ).hexdigest()
            if len(body) > MAX_ARTIFACT_BYTES:
                raise RuntimeError(
                    f"refusing to write oversized artifact {bucket}/{key}: {len(body)} bytes"
                )
            artifact = out / "artifacts" / bucket / key
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(body)
    for name in (
        "otterworks-analytics-events",
        "otterworks-audit-events",
        "otterworks-file-metadata",
    ):
        items = dynamo_scan_all(dynamo.Table(name))
        manifest["dynamodb"][name] = {
            "count": len(items),
            "ids": sorted(str(x.get("event_id", x.get("id", ""))) for x in items),
        }
    conn = psycopg2.connect(**pg_kwargs())
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM analytics_daily_summary")
        manifest["postgres"]["analytics_daily_summary_rows"] = cur.fetchone()[0]
        cur.execute("""SELECT report_date, active_users, active_documents, active_files,
            total_events, documents_created, documents_edited, comments_added,
            files_uploaded, files_shared, files_deleted, bytes_uploaded
            FROM analytics_daily_summary ORDER BY report_date""")
        manifest["postgres"]["analytics_daily_summary"] = [
            list(row) for row in cur.fetchall()
        ]
    conn.close()
    for index in ("documents", "files"):
        try:
            base = "http://127.0.0.1:7700/indexes/" + index
            manifest["meilisearch"][index] = {
                "settings": requests.get(base + "/settings").json(),
                "ids": sorted(
                    x["id"]
                    for x in requests.get(base + "/documents", params={"limit": 10000})
                    .json()
                    .get("results", [])
                ),
            }
        except requests.RequestException as exc:
            raise RuntimeError(f"MeiliSearch capture failed for {index}") from exc
    expectations = {
        "analytics_daily": ("Total events to process: ", "analytics events"),
        "storage_cleanup_daily": ("Quarantined ", "quarantined objects"),
        "audit_archive_weekly": ("Archived ", "archived audit records"),
        "user_activity_daily": ("Aggregated activity for ", "activity users"),
    }
    log = Path(__file__).parent / "state/logs" / f"{args.job}.log"
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    if args.job not in expectations:
        if (
            not manifest["meilisearch"]["documents"]["ids"]
            or not manifest["meilisearch"]["files"]["ids"]
        ):
            raise RuntimeError("search indexes are empty")
    else:
        marker, label = expectations[args.job]
        line = next((line for line in text.splitlines() if marker in line), "")
        digits = "".join(ch if ch.isdigit() else " " for ch in line).split()
        if not digits or int(digits[-1]) <= 0:
            raise RuntimeError(f"{label} expectation failed for {args.job}")
    write_json(out / "manifest.json", manifest)
    if log.exists():
        (out / "stdout.log").write_bytes(log.read_bytes())
    print(json.dumps(manifest, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()

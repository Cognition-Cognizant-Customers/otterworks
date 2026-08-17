#!/usr/bin/env python3
"""Extract the pre-run-date activity history into the Cron Box landing volume."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tp_cronbox.common import clients, pg_kwargs
from tp_dbx.client import Databricks, require_ns

BUCKET = "otterworks-data-lake"
ROOT = Path(".tp-preflight/databricks-fixture/landing")
RELATIVE = "Volumes/ow_tp/bronze/landing/cronbox/user-activity"
SNAPSHOTS = Path(".tp-preflight/databricks-fixture/activity-rerun-snapshots")

SUMMARY_FIELDS = (
    "active_users", "active_documents", "active_files", "total_events",
    "documents_created", "documents_edited", "comments_added", "files_uploaded",
    "files_shared", "files_deleted", "bytes_uploaded",
)


def dates(ds: str) -> list[date]:
    end = date.fromisoformat(ds)
    return [end - timedelta(days=i) for i in range(30)]


def round_daily_average(total: int, reporting_days: int) -> float:
    return 0.0 if reporting_days == 0 else round(total / reporting_days, 2)


def object_key(day: date) -> str:
    return f"analytics/daily/year={day:%Y}/month={day:%m}/day={day:%d}/top_users.jsonl.gz"


def summary_envelope(ns: str, row: dict, landing_ds: str) -> dict:
    return {
        "kind": "daily_summary", "namespace": ns,
        "landing_ds": landing_ds,
        "report_date": str(row["report_date"]),
        **{field: row[field] for field in SUMMARY_FIELDS},
        "source": "postgres:analytics_daily_summary", "source_line": row.get("source_line", 0),
    }


def parse_history(ns: str, day: date, key: str, raw: bytes, landing_ds: str) -> list[dict]:
    landing = landing_ds
    try:
        text = gzip.decompress(raw).decode("utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        return [{
            "kind": "top_user", "namespace": ns, "landing_ds": landing,
            "report_date": str(day),
            "source_object": key, "source_line": 1,
            "parse_error": type(exc).__name__, "raw_line": None,
        }]
    result = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
            if not isinstance(parsed, dict):
                raise ValueError("record is not an object")
            actions = parsed.get("actions", {})
            result.append({
                "kind": "top_user", "namespace": ns, "landing_ds": landing,
                "report_date": str(day),
                "user_id": parsed.get("user_id"), "total": parsed.get("total", 0),
                "actions_json": json.dumps(actions, ensure_ascii=False, separators=(",", ":")),
                "source_object": key, "source_line": line_no,
            })
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            result.append({
                "kind": "top_user", "namespace": ns, "landing_ds": landing,
                "report_date": str(day),
                "source_object": key, "source_line": line_no,
                "parse_error": type(exc).__name__, "raw_line": line,
            })
    return result


def aggregate_history(records: list[dict]) -> list[dict]:
    """Reference implementation of the legacy stable descending user sort."""
    values: dict[str, dict] = {}
    for record in records:
        if record.get("parse_error"):
            continue
        user_id = record.get("user_id") or "unknown"
        if user_id not in values:
            values[user_id] = {"user_id": user_id, "total_actions": 0, "active_days": 0,
                               "actions_by_type": {}, "first_seen_date": record["report_date"],
                               "first_seen_seq": record["source_line"]}
        value = values[user_id]
        value["total_actions"] += int(record.get("total", 0))
        value["active_days"] += 1
        for key, count in json.loads(record.get("actions_json", "{}")).items():
            value["actions_by_type"][key] = value["actions_by_type"].get(key, 0) + int(count)
    return sorted(values.values(), key=lambda item: item["total_actions"], reverse=True)


def extract(ns: str, ds: str) -> tuple[list[dict], list[dict], list[str]]:
    """Read summary rows and strictly pre-run-date history objects."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    end = date.fromisoformat(ds)
    wanted = {day for day in dates(ds) if day < end}
    summaries = []
    with psycopg2.connect(**pg_kwargs()) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT report_date, " + ", ".join(SUMMARY_FIELDS) +
                " FROM analytics_daily_summary WHERE report_date < %s "
                "AND report_date BETWEEN %s AND %s "
                "ORDER BY report_date",
                (end, end - timedelta(days=30), end - timedelta(days=1)),
            )
            summaries = [summary_envelope(ns, row, ds) for row in cur.fetchall()]
    s3, _, _ = clients()
    top_users = []
    missing = []
    for day in sorted(wanted, reverse=True):
        key = object_key(day)
        try:
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        except s3.exceptions.NoSuchKey:
            missing.append(str(day))
            continue
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey"}:
                raise
            missing.append(str(day))
            continue
        top_users.extend(parse_history(ns, day, key, body, ds))
    return summaries, top_users, missing


def payload(records: list[dict]) -> bytes:
    return b"".join(
        (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for record in records
    )


def land(ns: str, ds: str, summaries: list[dict], users: list[dict], missing: list[str], target: str) -> None:
    parts = {
        "history-summary-0000.jsonl": payload(summaries),
        "history-top-users-0000.jsonl": payload(users),
    }
    manifest = {
        "namespace": ns, "report_date": ds, "summary_records": len(summaries),
        "top_user_records": len(users), "missing_history_days": missing,
        "parts": {name: {"count": data.count(b"\n"), "sha256": hashlib.sha256(data).hexdigest()}
                  for name, data in parts.items()},
    }
    parts["_manifest.json"] = (json.dumps(manifest, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()
    if target == "stdout":
        for data in parts.values():
            sys.stdout.buffer.write(data)
        return
    if target == "local-fixture":
        directory = ROOT / RELATIVE / ns / ds
        directory.mkdir(parents=True, exist_ok=True)
        for name, data in parts.items():
            path = directory / name
            if path.exists():
                snapshot = SNAPSHOTS / ns / ds / name
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, snapshot)
            path.write_bytes(data)
        print(f"landed {len(summaries) + len(users)} records at {directory}")
        return
    prefix = f"/{RELATIVE}/{ns}/{ds}"
    dbx = Databricks()
    for name, data in parts.items():
        dbx.put_file(f"{prefix}/{name}", data)
    print(f"landed {len(summaries) + len(users)} records at {prefix}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--ds", default="2026-01-15")
    parser.add_argument("--target", choices=("local-fixture", "databricks", "stdout"), default="local-fixture")
    args = parser.parse_args()
    ns = require_ns(args.ns)
    summaries, users, missing = extract(ns, args.ds)
    land(ns, args.ds, summaries, users, missing, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

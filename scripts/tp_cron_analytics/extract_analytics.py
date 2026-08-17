#!/usr/bin/env python3
"""Extract Cron Box analytics inputs into the Databricks landing volume."""
from __future__ import annotations

import argparse
import base64
import decimal
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tp_cronbox.common import clients
from tp_dbx.client import Databricks, require_ns

QUEUE = "otterworks-analytics"
EVENTS_TABLE = "otterworks-analytics-events"
LANDING_ROOT = Path(".tp-preflight/databricks-fixture/landing")
RELATIVE_LANDING = "Volumes/ow_tp/bronze/landing/cronbox/analytics"
RERUN_SNAPSHOT_ROOT = Path(".tp-preflight/databricks-fixture/analytics-rerun-snapshots")


def json_default(value):
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def body_bytes(body) -> bytes:
    return body if isinstance(body, bytes) else str(body).encode("utf-8")


def envelope(namespace: str, report_date: str, source: str, source_id: str,
             source_seq: int, source_event_date: str | None, body) -> dict:
    raw = body_bytes(body)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None
        encoded = base64.b64encode(raw).decode("ascii")
        error = "invalid_utf8"
    else:
        encoded = None
        error = None
    return {
        "namespace": namespace,
        "report_date": report_date,
        "source": source,
        "source_id": source_id,
        "source_seq": source_seq,
        "source_event_date": source_event_date,
        "raw_body": text,
        "raw_body_b64": encoded,
        "decode_error": error,
    }


def source_id(body) -> str:
    raw = body_bytes(body)
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict) and parsed.get("event_id"):
        return str(parsed["event_id"])
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def extract(ns: str, ds: str) -> list[dict]:
    _, dynamo, sqs = clients()
    queue_url = sqs.get_queue_url(QueueName=QUEUE)["QueueUrl"]
    records = []
    # Bronze identity is (namespace, report_date, source, source_id), so dedup is
    # per source: an event carrying the same event_id on the queue and in the
    # table is two events, exactly as the legacy job's concatenation treated it.
    seen_sqs_ids: set[str] = set()
    seen_ddb_ids: set[str] = set()
    while True:
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            WaitTimeSeconds=1,
            VisibilityTimeout=600,
        )
        messages = response.get("Messages", [])
        if not messages:
            break
        for message in messages:
            body = message.get("Body", "")
            record_source_id = source_id(body)
            if record_source_id in seen_sqs_ids:
                continue
            seen_sqs_ids.add(record_source_id)
            records.append(envelope(ns, ds, "sqs", record_source_id, 0, None, body))

    table = dynamo.Table(EVENTS_TABLE)
    scan_kwargs = {"FilterExpression": "begins_with(event_date, :month)"}
    items = []
    while True:
        page = table.scan(
            **scan_kwargs,
            ExpressionAttributeValues={":month": ds[:7]},
        )
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        scan_kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    prefix = ns + "-"
    for item in items:
        event_id = str(item.get("event_id", ""))
        if event_id.startswith(prefix):
            if event_id in seen_ddb_ids:
                continue
            seen_ddb_ids.add(event_id)
            body = json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                default=json_default,
            )
            records.append(
                envelope(
                    ns, ds, "dynamodb", event_id, 0,
                    str(item["event_date"]) if "event_date" in item else None, body,
                )
            )
    for sequence, record in enumerate(records):
        record["source_seq"] = sequence
    return records


def payload(records: list[dict]) -> bytes:
    return b"".join(
        (json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for record in records
    )


def local_path(ns: str, ds: str) -> Path:
    return LANDING_ROOT / RELATIVE_LANDING / ns / ds / "part-0000.jsonl"


def rerun_snapshot_path(ns: str, ds: str) -> Path:
    return RERUN_SNAPSHOT_ROOT / ns / ds / "part-0000.jsonl.previous"


def land(ns: str, ds: str, records: list[dict], target: str) -> Path | None:
    if not records:
        print(f"warning: no analytics inputs found for namespace={ns} ds={ds}")
        return None
    content = payload(records)
    relative = f"/{RELATIVE_LANDING}/{ns}/{ds}/part-0000.jsonl"
    if target == "stdout":
        sys.stdout.buffer.write(content)
        return None
    if target == "local-fixture":
        path = local_path(ns, ds)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            snapshot = rerun_snapshot_path(ns, ds)
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, snapshot)
        path.write_bytes(content)
        print(f"landed {len(records)} records at {path}")
        return path
    Databricks().put_file(relative, content)
    print(f"landed {len(records)} records at {relative}")
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--ds", default="2026-01-15")
    parser.add_argument("--target", choices=("local-fixture", "databricks", "stdout"), default="local-fixture")
    args = parser.parse_args()
    ns = require_ns(args.ns)
    records = extract(ns, args.ds)
    land(ns, args.ds, records, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

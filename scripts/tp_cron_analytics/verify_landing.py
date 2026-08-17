#!/usr/bin/env python3
"""Verify the Cron Box analytics transport fixture and emit a recon report."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from extract_analytics import local_path, rerun_snapshot_path

OUT = Path("docs/tech-partnerships/recon/cron-analytics-demo.fixture.recon.json")


def read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def canonical(records: list[dict]) -> list[dict]:
    return sorted(records, key=lambda r: (r["source"], r["source_id"]))


def check(check_id: str, expected, actual, source: str) -> dict:
    return {
        "id": check_id,
        "expected": expected,
        "actual": actual,
        "source_of_truth": source,
        "result": "pass" if expected == actual else "fail",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--ds", default="2026-01-15")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()
    path = local_path(args.ns, args.ds)
    source = str(path)
    if not path.exists():
        raise SystemExit(f"missing landed fixture: {path}")
    records = read_records(path)
    sqs = [r for r in records if r["source"] == "sqs"]
    ddb = [r for r in records if r["source"] == "dynamodb"]
    parseable_sqs = [r for r in sqs if r["decode_error"] is None and r["raw_body"] is not None]
    parsed = []
    for record in parseable_sqs:
        try:
            value = json.loads(record["raw_body"])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            parsed.append((record, value))
    day_counts = {}
    for record in ddb:
        day = str(record["source_event_date"])[:10]
        day_counts[day] = day_counts.get(day, 0) + 1
    unknown = sum(
        not any(value.get(field) for field in ("ownerId", "editedBy", "authorId", "deletedBy", "userId"))
        for _, value in parsed
    )
    titles = sum(value.get("title") == "Réunion café ☕" for _, value in parsed)
    names = sum(value.get("name") == "Δelta" for _, value in parsed)
    replacement = sum("\ufffd" in (record["raw_body"] or "") for record in records)
    seqs = [record["source_seq"] for record in records]
    sqs_seqs = [record["source_seq"] for record in sqs]
    ddb_seqs = [record["source_seq"] for record in ddb]
    checks = [
        check("LAND-01", 296, len(records), source),
        check("LAND-02", {"records": 248, "non_json_bodies": 8}, {"records": len(sqs), "non_json_bodies": len(sqs) - len(parsed)}, source),
        check("LAND-03", {"records": 48, "2026-01-15": 32, "2026-01-14": 8, "2026-01-16": 8}, {"records": len(ddb), **day_counts}, source),
        check("LAND-04", 22, unknown, source),
        check("LAND-05", {"titles": 15, "names": 13, "replacement_chars": 0}, {"titles": titles, "names": names, "replacement_chars": replacement}, source),
        check("LAND-06", True, seqs == list(range(len(records))) and (not sqs_seqs or not ddb_seqs or max(sqs_seqs) < min(ddb_seqs)), source),
    ]
    previous = rerun_snapshot_path(args.ns, args.ds)
    rerun = {"performed": previous.exists(), "result": "fail"}
    if previous.exists():
        old_records = read_records(previous)
        equal = canonical(old_records) == canonical(records)
        rerun["result"] = "pass" if equal else "fail"
        rerun["evidence"] = (
            f"sha256:{hashlib.sha256(previous.read_bytes()).hexdigest()} "
            f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        )
    expected_anomalies = [
        ["malformed_sqs_bodies", "unparseable_json_body", 8],
        ["unknown_user_events", "unknown", 22],
        ["dynamodb_adjacent_day_events", "2026-01-14", 8],
        ["dynamodb_adjacent_day_events", "2026-01-16", 8],
        ["unicode_payloads", "Réunion café ☕", 15],
        ["unicode_payloads", "Δelta", 13],
    ]
    actual_anomalies = [
        ["malformed_sqs_bodies", "unparseable_json_body", len(sqs) - len(parsed)],
        ["unknown_user_events", "unknown", unknown],
        ["dynamodb_adjacent_day_events", "2026-01-14", day_counts.get("2026-01-14", 0)],
        ["dynamodb_adjacent_day_events", "2026-01-16", day_counts.get("2026-01-16", 0)],
        ["unicode_payloads", "Réunion café ☕", titles],
        ["unicode_payloads", "Δelta", names],
    ]
    expected_set = {tuple(item) for item in expected_anomalies}
    actual_set = {tuple(item) for item in actual_anomalies}
    report = {
        "kind": "recon-report",
        "unit": "cron-analytics",
        "namespace": args.ns,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_mode": "fixture",
        "checks": checks,
        "values_recomputed_from_target": False,
        "idempotency_rerun": rerun,
        "planted_anomaly_detections": {
            "expected_set": [list(item) for item in sorted(expected_set)],
            "actual_set": [list(item) for item in sorted(actual_set)],
            "missing": [list(item) for item in sorted(expected_set - actual_set)],
            "unexpected": [list(item) for item in sorted(actual_set - expected_set)],
        },
        "unverified_paths": [
            "This fixture report proves transport/landing only.",
            "Every Spark SQL, Delta MERGE, gold aggregate, and Unity Catalog behaviour is proven only by the live recon the parent runs.",
        ],
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failures = [item["id"] for item in checks if item["result"] != "pass"]
    if not rerun["performed"] or rerun["result"] != "pass":
        failures.append("idempotency_rerun")
    if expected_set != actual_set:
        failures.append("planted_anomaly_detections")
    print(f"fixture recon written: {output}")
    if failures:
        print("fixture recon mismatches: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

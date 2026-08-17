#!/usr/bin/env python3
"""Live recon for the cron-analytics unit of the Cron Box retirement.

Run by the parent session during its single live validation window:

    make tp-cron-analytics-recon NS=demo DS=2026-01-15 \
        OUT=docs/tech-partnerships/recon/cron-analytics-demo.recon.json

Every `actual` in the emitted report is recomputed by querying the deployed
Databricks target (warehouse SQL over the ow_tp bronze/silver/gold objects, the
Jobs API for the job shape, the Files API for the landing volume). Nothing is
read from Terraform state, bundle output, a job log line, or the local fixture.

Every `expected` comes from the immutable golden baseline captured from the
unmodified legacy run (`testdata/legacy/golden/cronbox/demo/analytics_daily/`)
or from the unit contract, never from the platform.

Writes: none, except the one the contract's ANL-06 idempotency check requires —
a rerun of the unit's own PAUSED job for the same run date, which rewrites only
this unit's ow_tp tables for that (namespace, report_date) slice. No other
object is created, and nothing outside the unit's prefix is touched.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from tp_dbx.client import Databricks, DbxError, require_ns

UNIT = "cron-analytics"
JOB_NAME = "ow_tp_cron_analytics_daily"
CONTRACT = REPO_ROOT / "docs/tech-partnerships/contracts/cron-analytics.json"
SQL_DIR = REPO_ROOT / "infrastructure/databricks/cronbox/src/analytics"
DS_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
REPLACEMENT_CHAR = "\ufffd"

# Fixed by the contract's encoding_policy: these payloads must survive
# ingestion and aggregation with their code points intact. The expected UTF-8
# bytes are derived from the literals here rather than transcribed by hand.
UNICODE_PAYLOADS = ["R\u00e9union caf\u00e9 \u2615", "\u0394elta"]

SUMMARY_FIELDS = [
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

TARGET_TABLES = [
    ("bronze", "cronbox_events_raw"),
    ("bronze", "cronbox_events_rejected"),
    ("silver", "cronbox_events"),
    ("gold", "analytics_daily_summary"),
    ("gold", "analytics_daily_top_users"),
    ("gold", "analytics_hourly_breakdown"),
    ("gold", "analytics_daily_report"),
]

Q_BY_SOURCE = """
SELECT source, COUNT(*) AS events
FROM ow_tp.silver.cronbox_events
WHERE namespace = '{ns}' AND report_date = DATE '{ds}'
GROUP BY source
ORDER BY source
"""

Q_SUMMARY = """
SELECT {fields}
FROM ow_tp.gold.analytics_daily_summary
WHERE namespace = '{ns}' AND report_date = DATE '{ds}'
"""

Q_SUMMARY_ROWS = """
SELECT COUNT(*) AS rows_for_date
FROM ow_tp.gold.analytics_daily_summary
WHERE namespace = '{ns}' AND report_date = DATE '{ds}'
"""

Q_TOP_USERS = """
SELECT user_id, total_actions, user_rank, TO_JSON(action_counts) AS actions_json
FROM ow_tp.gold.analytics_daily_top_users
WHERE namespace = '{ns}' AND report_date = DATE '{ds}'
ORDER BY user_rank
"""

Q_HOURLY = """
SELECT event_hour, event_type, event_count
FROM ow_tp.gold.analytics_hourly_breakdown
WHERE namespace = '{ns}' AND report_date = DATE '{ds}'
ORDER BY event_hour, event_type
"""

Q_REJECTED = """
SELECT reason, COUNT(*) AS bodies
FROM ow_tp.bronze.cronbox_events_rejected
WHERE namespace = '{ns}' AND report_date = DATE '{ds}'
GROUP BY reason
ORDER BY reason
"""

Q_UNKNOWN_USER = """
SELECT COUNT(*) AS events
FROM ow_tp.silver.cronbox_events
WHERE namespace = '{ns}' AND report_date = DATE '{ds}'
  AND resolved_user_id = 'unknown'
"""

Q_BRONZE_DDB = """
SELECT COUNT(*) AS records
FROM ow_tp.bronze.cronbox_events_raw
WHERE namespace = '{ns}' AND report_date = DATE '{ds}' AND source = 'dynamodb'
"""

# The adjacent-day events land in bronze (so they stay auditable) and are
# excluded by the silver run-date prefix rule, exactly as the legacy
# begins_with(event_date, ds) scan filter did. "Excluded" is therefore
# recomputed as: in bronze for this batch, absent from silver.
Q_EXCLUDED_DAYS = """
SELECT SUBSTRING(r.source_event_date, 1, 10) AS event_day, COUNT(*) AS events
FROM ow_tp.bronze.cronbox_events_raw r
WHERE r.namespace = '{ns}'
  AND r.report_date = DATE '{ds}'
  AND r.source = 'dynamodb'
  AND NOT EXISTS (
    SELECT 1
    FROM ow_tp.silver.cronbox_events s
    WHERE s.namespace = r.namespace
      AND s.report_date = r.report_date
      AND s.source = r.source
      AND s.source_id = r.source_id
  )
GROUP BY SUBSTRING(r.source_event_date, 1, 10)
ORDER BY event_day
"""

Q_UNICODE = """
SELECT 'title' AS field, title AS value, HEX(ENCODE(title, 'UTF-8')) AS utf8_hex, COUNT(*) AS events
FROM ow_tp.silver.cronbox_events
WHERE namespace = '{ns}' AND report_date = DATE '{ds}' AND title IN ({literals})
GROUP BY title
UNION ALL
SELECT 'name' AS field, name AS value, HEX(ENCODE(name, 'UTF-8')) AS utf8_hex, COUNT(*) AS events
FROM ow_tp.silver.cronbox_events
WHERE namespace = '{ns}' AND report_date = DATE '{ds}' AND name IN ({literals})
GROUP BY name
ORDER BY field, value
"""

Q_REPLACEMENT = """
SELECT COUNT_IF(
  CONTAINS(COALESCE(title, ''), '{char}') OR CONTAINS(COALESCE(name, ''), '{char}')
) AS rows_with_replacement_char
FROM ow_tp.silver.cronbox_events
WHERE namespace = '{ns}' AND report_date = DATE '{ds}'
"""

Q_TABLES = """
SELECT table_schema, table_name
FROM ow_tp.information_schema.tables
WHERE table_schema IN ('bronze', 'silver', 'gold')
  AND table_name IN ({names})
ORDER BY table_schema, table_name
"""

Q_REPORT = """
SELECT report_json
FROM ow_tp.gold.analytics_daily_report
WHERE namespace = '{ns}' AND report_date = DATE '{ds}'
"""


def sql_literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def as_int(value) -> int:
    return 0 if value in (None, "") else int(value)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


# --- expected side: immutable golden baseline + contract ---------------------


def load_baseline(baseline_dir: Path, ds: str) -> dict:
    lake = baseline_dir / "artifacts/otterworks-data-lake"
    partition = f"year={ds[:4]}/month={ds[5:7]}/day={ds[8:10]}"
    daily = lake / "analytics/daily" / partition

    def gz_json(path: Path):
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))

    users = [
        json.loads(line)
        for line in gzip.decompress((daily / "top_users.jsonl.gz").read_bytes())
        .decode("utf-8")
        .splitlines()
        if line.strip()
    ]
    report_rel = f"otterworks-data-lake/reports/analytics/daily/{ds}/report.json"
    return {
        "summary": gz_json(daily / "summary.json.gz"),
        "hourly": gz_json(daily / "hourly_breakdown.json.gz"),
        "top_users": users,
        "report": json.loads((lake / f"reports/analytics/daily/{ds}/report.json").read_text("utf-8")),
        "manifest": json.loads((baseline_dir / "manifest.json").read_text("utf-8")),
        "report_rel": report_rel,
    }


def volatile_fields(manifest: dict, report_rel: str) -> list:
    declared = manifest.get("volatile_fields", {}).get(report_rel, [])
    if isinstance(declared, dict):
        return sorted(declared)
    if isinstance(declared, str):
        return [declared]
    return list(declared)


def ddb_day_counts(manifest: dict, ns: str, ds: str) -> dict:
    """Baseline DynamoDB ids encode which day bucket the seed put them in:
    `<ns>-ddb-<offset>-<n>` with offset 0 = ds-1, 1 = ds, 2 = ds+1."""
    ids = manifest["dynamodb"]["otterworks-analytics-events"]["ids"]
    anchor = date.fromisoformat(ds)
    pattern = re.compile(rf"{re.escape(ns)}-ddb-([012])-\d+$")
    counts: dict[str, int] = {}
    for event_id in ids:
        match = pattern.fullmatch(event_id)
        if not match:
            continue
        day = (anchor + timedelta(days=int(match.group(1)) - 1)).isoformat()
        counts[day] = counts.get(day, 0) + 1
    return counts


def top_users_set(rows: list) -> list:
    """(user_id, total, sorted action counts) tuples; the legacy artifact's key
    order carries no meaning, so comparison is order-insensitive."""
    out = []
    for row in rows:
        actions = sorted((k, int(v)) for k, v in row["actions"].items())
        out.append([row["user_id"], int(row["total"]), [list(item) for item in actions]])
    return sorted(out, key=canonical)


# --- actual side: the deployed target ---------------------------------------


class Target:
    def __init__(self, dbx: Databricks, ns: str, ds: str):
        self.dbx = dbx
        self.ns = ns
        self.ds = ds

    def query(self, template: str, **extra) -> list[dict]:
        statement = template.format(ns=self.ns, ds=self.ds, **extra)
        return self.dbx.sql_ok(statement).dicts()

    def snapshot(self) -> dict:
        by_source = {row["source"]: as_int(row["events"]) for row in self.query(Q_BY_SOURCE)}

        summary_rows = self.query(Q_SUMMARY, fields=", ".join(SUMMARY_FIELDS))
        summary = {field: as_int(summary_rows[0][field]) for field in SUMMARY_FIELDS} if summary_rows else {}

        hourly: dict[str, dict[str, int]] = {}
        for row in self.query(Q_HOURLY):
            hourly.setdefault(row["event_hour"], {})[row["event_type"]] = as_int(row["event_count"])

        top_users = [
            {
                "user_id": row["user_id"],
                "total": as_int(row["total_actions"]),
                "rank": as_int(row["user_rank"]),
                "actions": json.loads(row["actions_json"]),
            }
            for row in self.query(Q_TOP_USERS)
        ]

        literals = ", ".join(sql_literal(value) for value in UNICODE_PAYLOADS)
        unicode_rows = [
            {
                "field": row["field"],
                "value": row["value"],
                "utf8_hex": (row["utf8_hex"] or "").upper(),
                "events": as_int(row["events"]),
            }
            for row in self.query(Q_UNICODE, literals=literals)
        ]

        report_rows = self.query(Q_REPORT)
        table_names = ", ".join(sql_literal(name) for _, name in TARGET_TABLES)

        return {
            "events_by_source": by_source,
            "total_events": sum(by_source.values()),
            "summary": summary,
            "summary_rows_for_date": as_int(self.query(Q_SUMMARY_ROWS)[0]["rows_for_date"]),
            "hourly": hourly,
            "top_users": top_users,
            "rejected_by_reason": {row["reason"]: as_int(row["bodies"]) for row in self.query(Q_REJECTED)},
            "unknown_user_events": as_int(self.query(Q_UNKNOWN_USER)[0]["events"]),
            "bronze_dynamodb_records": as_int(self.query(Q_BRONZE_DDB)[0]["records"]),
            "excluded_by_day": {
                row["event_day"]: as_int(row["events"]) for row in self.query(Q_EXCLUDED_DAYS)
            },
            "unicode": unicode_rows,
            "rows_with_replacement_char": as_int(
                self.query(Q_REPLACEMENT, char=REPLACEMENT_CHAR)[0]["rows_with_replacement_char"]
            ),
            "tables": [
                f"{row['table_schema']}.{row['table_name']}"
                for row in self.query(Q_TABLES, names=table_names)
            ],
            "report": json.loads(report_rows[0]["report_json"]) if report_rows else {},
        }

    def job_shape(self) -> dict:
        job = self.dbx.find_job(JOB_NAME)
        if not job:
            return {"found": False, "pause_status": "", "compute": "", "job_id": 0}
        job_id = int(job["job_id"])
        settings = self.dbx.ok("GET", f"/api/2.1/jobs/get?job_id={job_id}").get("settings", {})
        violations = []
        if settings.get("job_clusters"):
            violations.append("job_clusters")
        for task in settings.get("tasks", []):
            for forbidden in ("new_cluster", "existing_cluster_id", "job_cluster_key"):
                if task.get(forbidden):
                    violations.append(f"{task.get('task_key', '?')}.{forbidden}")
            sql_task = task.get("sql_task") or {}
            if not sql_task.get("warehouse_id"):
                violations.append(f"{task.get('task_key', '?')}.no_sql_warehouse")
        return {
            "found": True,
            "job_id": job_id,
            "pause_status": settings.get("schedule", {}).get("pause_status", "NONE"),
            "compute": "sql_warehouse_only" if not violations else "violations: " + ", ".join(sorted(violations)),
            "tasks": len(settings.get("tasks", [])),
        }

    def landed_files(self) -> int:
        path = f"/Volumes/ow_tp/bronze/landing/cronbox/analytics/{self.ns}/{self.ds}"
        return len([entry for entry in self.dbx.list_dir(path) if not entry.get("is_directory")])


# --- the ANL-06 rerun -------------------------------------------------------


def split_statements(text: str) -> list[str]:
    """The SQL files are one statement each; this only strips the trailing
    semicolon and comment-only tails so the statements API accepts them."""
    body = text.strip()
    return [body[:-1].strip()] if body.endswith(";") else [body]


def rerun_job(target: Target, job_shape: dict) -> tuple[bool, str]:
    if not job_shape.get("found"):
        return False, f"job {JOB_NAME} not found in the workspace"
    run_id = target.dbx.run_job(job_shape["job_id"], {"ns": target.ns, "ds": target.ds})
    run = target.dbx.wait_run(run_id)
    state = run.get("state", {})
    result = state.get("result_state", "")
    evidence = (
        f"reran deployed job {JOB_NAME} (run {run_id}, {target.dbx.run_url(run_id)}) "
        f"for ns={target.ns} ds={target.ds}: result_state={result or state.get('life_cycle_state', '?')}"
    )
    return result == "SUCCESS", evidence


def rerun_sql(target: Target) -> tuple[bool, str]:
    files = sorted(path for path in SQL_DIR.glob("*.sql"))
    executed = []
    for path in files:
        for statement in split_statements(path.read_text("utf-8")):
            rendered = statement.replace(":ns", sql_literal(target.ns)).replace(":ds", sql_literal(target.ds))
            target.dbx.sql_ok(rendered)
        executed.append(path.name)
    return True, "re-executed the unit's SQL on the serverless warehouse: " + ", ".join(executed)


# --- report assembly --------------------------------------------------------


def check(checks: list, check_id: str, expected, actual, source: str, result: str | None = None) -> None:
    outcome = result or ("pass" if canonical(expected) == canonical(actual) else "fail")
    checks.append(
        {
            "id": check_id,
            "expected": expected,
            "actual": actual,
            "source_of_truth": source,
            "result": outcome,
        }
    )


def build_checks(baseline: dict, contract: dict, after: dict, job_shape: dict, landed: int, ns: str, ds: str) -> list:
    checks: list = []
    golden = "immutable golden baseline captured from the unmodified legacy analytics_daily.py run"
    golden_summary = f"{golden} (analytics/daily/**/summary.json.gz)"
    contract_src = "docs/tech-partnerships/contracts/cron-analytics.json"

    day_counts = ddb_day_counts(baseline["manifest"], ns, ds)
    ddb_for_ds = day_counts.get(ds, 0)
    baseline_total = int(baseline["summary"]["total_events"])

    check(checks, "ANL-01/total_events", baseline_total, after["total_events"], golden_summary)
    check(
        checks,
        "ANL-01/events_from_sqs",
        baseline_total - ddb_for_ds,
        after["events_by_source"].get("sqs", 0),
        f"{golden} (total_events minus the run-date DynamoDB ids in manifest.json)",
    )
    check(
        checks,
        "ANL-01/events_from_dynamodb",
        ddb_for_ds,
        after["events_by_source"].get("dynamodb", 0),
        f"{golden} (manifest.json otterworks-analytics-events ids for the run date)",
    )

    for field in SUMMARY_FIELDS:
        check(
            checks,
            f"ANL-02/{field}",
            int(baseline["summary"][field]),
            after["summary"].get(field),
            golden_summary,
        )

    check(
        checks,
        "ANL-03/top_users_set",
        top_users_set(baseline["top_users"]),
        top_users_set(
            [{"user_id": row["user_id"], "total": row["total"], "actions": row["actions"]} for row in after["top_users"]]
        ),
        f"{golden} (analytics/daily/**/top_users.jsonl.gz)",
    )
    check(
        checks,
        "ANL-03/unknown_user_events",
        contract["malformed_record_policy"]["expected_unknown_user_events"],
        after["unknown_user_events"],
        f"{contract_src} malformed_record_policy.expected_unknown_user_events (agrees with the baseline top_users 'unknown' total)",
    )
    check(
        checks,
        "ANL-03/malformed_bodies_attributed",
        contract["malformed_record_policy"]["expected_malformed_count"],
        sum(after["rejected_by_reason"].values()),
        f"{contract_src} malformed_record_policy.expected_malformed_count",
    )

    check(
        checks,
        "ANL-04/bronze_dynamodb_records",
        int(baseline["manifest"]["dynamodb"]["otterworks-analytics-events"]["count"]),
        after["bronze_dynamodb_records"],
        f"{golden} (manifest.json otterworks-analytics-events count)",
    )
    adjacent = {day: count for day, count in day_counts.items() if day != ds}
    check(
        checks,
        "ANL-04/excluded_adjacent_days",
        adjacent,
        {day: count for day, count in after["excluded_by_day"].items() if day != ds},
        f"{golden} (manifest.json DynamoDB ids bucketed by seeded day offset)",
    )
    check(
        checks,
        "ANL-04/excluded_total",
        sum(adjacent.values()),
        sum(after["excluded_by_day"].values()),
        f"{golden} (manifest.json DynamoDB ids outside the run date)",
    )

    check(
        checks,
        "ANL-05/hourly_breakdown",
        baseline["hourly"],
        after["hourly"],
        f"{golden} (analytics/daily/**/hourly_breakdown.json.gz)",
    )
    check(
        checks,
        "ANL-05/top_users_row_count",
        len(baseline["top_users"]),
        len(after["top_users"]),
        f"{golden} (analytics/daily/**/top_users.jsonl.gz line count)",
    )
    check(
        checks,
        "ANL-05/unicode_utf8_bytes",
        sorted([value, value.encode("utf-8").hex().upper()] for value in UNICODE_PAYLOADS),
        sorted({canonical([row["value"], row["utf8_hex"]]): [row["value"], row["utf8_hex"]] for row in after["unicode"]}.values(), key=canonical),
        f"{contract_src} encoding_policy.byte_transparency (UTF-8 bytes of the seeded literals)",
    )
    check(
        checks,
        "ANL-05/replacement_characters",
        0,
        after["rows_with_replacement_char"],
        f"{contract_src} encoding_policy.invalid_bytes (never replacement-decode)",
    )

    check(
        checks,
        "ANL-06/gold_summary_rows_for_date",
        1,
        after["summary_rows_for_date"],
        f"{contract_src} ANL-06 (legacy ON CONFLICT (report_date) DO UPDATE)",
    )

    expected_report = {
        key: value for key, value in baseline["report"].items() if key not in volatile_fields(baseline["manifest"], baseline["report_rel"])
    }
    check(
        checks,
        "ANL-07/report_content",
        expected_report,
        after["report"],
        f"{golden} (reports/analytics/daily/{ds}/report.json, declared volatile fields removed)",
    )
    check(
        checks,
        "ANL-07/generated_at_excluded",
        "<not compared: coverage_gap report_generated_at_wall_clock>",
        baseline["report"].get("generated_at", ""),
        f"{contract_src} planted_anomalies.report_generated_at_wall_clock",
        result="skipped",
    )

    check(
        checks,
        "TARGET/objects_present",
        sorted(f"{schema}.{name}" for schema, name in TARGET_TABLES),
        sorted(after["tables"]),
        "ow_tp.information_schema.tables on the deployed workspace",
    )
    check(
        checks,
        "TARGET/job_schedule_paused",
        "PAUSED",
        job_shape.get("pause_status", ""),
        f"Databricks Jobs API settings for {JOB_NAME}",
    )
    check(
        checks,
        "TARGET/job_compute_serverless_only",
        "sql_warehouse_only",
        job_shape.get("compute", ""),
        f"Databricks Jobs API settings for {JOB_NAME} (no cluster, no job compute)",
    )
    check(
        checks,
        "TARGET/landing_volume_populated",
        True,
        landed > 0,
        f"Databricks Files API listing of /Volumes/ow_tp/bronze/landing/cronbox/analytics/{ns}/{ds}",
    )
    return checks


def anomaly_sets(baseline: dict, contract: dict, after: dict, ns: str, ds: str) -> dict:
    policy = contract["malformed_record_policy"]
    expected = [
        ["malformed_sqs_bodies", "unparseable_json_body", policy["expected_malformed_count"]],
        ["unknown_user_events", "unknown", policy["expected_unknown_user_events"]],
    ]
    for day, count in sorted(ddb_day_counts(baseline["manifest"], ns, ds).items()):
        if day != ds:
            expected.append(["dynamodb_adjacent_day_events", day, count])
    for value in UNICODE_PAYLOADS:
        expected.append(["unicode_payloads", value, value.encode("utf-8").hex().upper()])

    actual = [
        ["malformed_sqs_bodies", reason, count] for reason, count in sorted(after["rejected_by_reason"].items())
    ]
    actual.append(["unknown_user_events", "unknown", after["unknown_user_events"]])
    for day, count in sorted(after["excluded_by_day"].items()):
        if day != ds:
            actual.append(["dynamodb_adjacent_day_events", day, count])
    seen = set()
    for row in after["unicode"]:
        entry = ["unicode_payloads", row["value"], row["utf8_hex"]]
        if canonical(entry) not in seen:
            seen.add(canonical(entry))
            actual.append(entry)

    expected_keys = {canonical(entry) for entry in expected}
    actual_keys = {canonical(entry) for entry in actual}
    return {
        "expected_set": expected,
        "actual_set": actual,
        "missing": [entry for entry in expected if canonical(entry) not in actual_keys],
        "unexpected": [entry for entry in actual if canonical(entry) not in expected_keys],
    }


def unverified(ns: str, ds: str, rerun_mode: str) -> list[str]:
    return [
        "gzip byte-parity of the legacy S3 artifacts (analytics/daily/**/summary.json.gz, hourly_breakdown.json.gz, top_users.jsonl.gz): the replacement publishes Delta tables and a gold view instead of gzip objects, so parity is asserted over decoded content, not bytes",
        "the legacy Postgres analytics_daily_summary.updated_at value (coverage_gap postgres_updated_at_wall_clock: database NOW(), not reconcilable)",
        "the legacy report generated_at value (coverage_gap report_generated_at_wall_clock: datetime.now() with fractional seconds)",
        "AWS-side extraction behaviour: the SQS drain/receive path and the DynamoDB begins_with scan run on the cron box before landing, so this recon proves the landed batch and everything downstream of it, not the AWS API calls themselves",
        "legacy SQS message deletion: the extractor deliberately does not delete messages (idempotent re-extraction via stable source_id), so the legacy delete-batch path is intentionally not reproduced",
        f"schedule firing: the job stays PAUSED by contract, so only an explicit run for ns={ns} ds={ds} is exercised, never the 02:00 UTC trigger",
        "Unity Catalog grants/least-privilege on the ow_tp objects: readable but not asserted by this recon",
    ] + (
        []
        if rerun_mode == "job"
        else ["idempotency was rerun by re-executing the unit SQL on the warehouse rather than by rerunning the deployed job"]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Live recon for the cron-analytics unit")
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--ds", default="2026-01-15")
    parser.add_argument("--out", default="docs/tech-partnerships/recon/cron-analytics-demo.recon.json")
    parser.add_argument(
        "--baseline",
        default="testdata/legacy/golden/cronbox/demo/analytics_daily",
        help="immutable golden baseline directory (expected values)",
    )
    parser.add_argument("--warehouse-id", default="", help="serverless SQL warehouse id (else discovered)")
    parser.add_argument(
        "--rerun-mode",
        choices=["job", "sql"],
        default="job",
        help="how the ANL-06 idempotency rerun is performed; 'job' reruns the deployed PAUSED job",
    )
    args = parser.parse_args()

    ns = require_ns(args.ns)
    if not DS_RE.fullmatch(args.ds):
        raise SystemExit(f"--ds must be YYYY-MM-DD: {args.ds!r}")

    baseline_dir = Path(args.baseline)
    if not baseline_dir.is_absolute():
        baseline_dir = REPO_ROOT / baseline_dir
    baseline = load_baseline(baseline_dir, args.ds)
    contract = json.loads(CONTRACT.read_text("utf-8"))

    dbx = Databricks(warehouse_id=args.warehouse_id or None)
    target = Target(dbx, ns, args.ds)

    before = target.snapshot()
    job_shape = target.job_shape()
    rerun_ok, evidence = rerun_job(target, job_shape) if args.rerun_mode == "job" else rerun_sql(target)
    after = target.snapshot()

    unchanged = canonical(before) == canonical(after)
    if not unchanged:
        changed = sorted(key for key in after if canonical(before.get(key)) != canonical(after[key]))
        evidence += "; values changed across the rerun: " + ", ".join(changed)
    else:
        evidence += "; all recomputed values identical across the rerun"

    checks = build_checks(baseline, contract, after, job_shape, target.landed_files(), ns, args.ds)
    check(
        checks,
        "ANL-06/values_unchanged_after_rerun",
        "identical",
        "identical" if unchanged else "changed",
        "two full recomputations from the deployed target, around a rerun of the deployed job",
    )

    anomalies = anomaly_sets(baseline, contract, after, ns, args.ds)
    report = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": ns,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": "live",
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if (rerun_ok and unchanged) else "fail",
            "evidence": evidence,
        },
        "planted_anomaly_detections": anomalies,
        "unverified_paths": unverified(ns, args.ds, args.rerun_mode),
    }

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    failed = [item["id"] for item in checks if item["result"] == "fail"]
    green = not failed and rerun_ok and unchanged and not anomalies["missing"] and not anomalies["unexpected"]
    print(f"wrote {out_path}")
    print(f"checks: {len(checks)} total, {len(failed)} failed, {sum(1 for c in checks if c['result'] == 'skipped')} skipped")
    if failed:
        print("failed checks: " + ", ".join(failed))
    if anomalies["missing"] or anomalies["unexpected"]:
        print(f"anomaly set drift: missing={anomalies['missing']} unexpected={anomalies['unexpected']}")
    print("recon result: " + ("GREEN" if green else "RED"))
    return 0 if green else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DbxError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(2)

#!/usr/bin/env python3
"""Reconcile `ow_tp_analytics_daily` against the legacy `analytics_daily.py` output.

Runs the five numbered acceptance checks from
`docs/tech-partnerships/contracts/analytics_daily.md` and prints a report whose first line
states the baseline tier verbatim. Nothing here is allowed to make a check pass: the
baseline is read from the captured legacy run, comparisons are exact integer equality, and
a check that cannot be executed is reported `BLOCKED` with the command and the error.

Usage:
    NS=demo python3 scripts/tp_databricks/recon_analytics_daily.py [--write] [--baseline-dir DIR]
    make dbx-recon UNIT=analytics_daily NS=demo

`--write` also writes the report to docs/tech-partnerships/recon/analytics_daily.md.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pipeline_analytics_daily as runner  # noqa: E402  (path shim above)

dbx = runner.dbx
pipeline = runner.pipeline

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "docs" / "tech-partnerships" / "recon" / "analytics_daily.md"
DEFAULT_BASELINE_DIR = Path(os.environ.get("BASELINE_DIR", "/home/ubuntu/tp-golden/python/analytics_daily"))
BASELINE_TIER = "baseline: legacy output"
PROBE_NS_PREFIX = "recon_probe"

# The legacy script reads camelCase fields (`eventType`, `timestamp`, `ownerId`,
# `documentId`, `fileId`) from each message; the seeded records carry `event_type`,
# `occurred_at`, `user_id`, `resource_id`. The legacy run therefore counted every event but
# attributed none of them: one `"00"` hour bucket and a single `unknown` user. That is a
# property of the baseline, not of the conversion, and check 2 reports it as a failure of
# exact group parity rather than relaxing the comparison.
LEGACY_ATTRIBUTION_NOTE = (
    "legacy aggregate carries no summary_date/document_id, one synthetic hour bucket "
    "'00' and a single user_id 'unknown'"
)


class Check:
    def __init__(self, number: int, title: str):
        self.number = number
        self.title = title
        self.status = "BLOCKED"
        self.lines: list[str] = []

    def record(self, label: str, baseline: object, converted: object, *, must_match: bool = True) -> bool:
        equal = baseline == converted
        marker = "=" if equal else "!="
        self.lines.append(f"{label}: baseline={baseline!r} {marker} converted={converted!r}")
        return equal or not must_match

    def note(self, text: str) -> None:
        self.lines.append(text)

    def resolve(self, passed: bool) -> None:
        self.status = "PASS" if passed else "FAIL"

    def block(self, command: str, error: str) -> None:
        self.status = "BLOCKED"
        self.lines.append(f"command: {command}")
        self.lines.append(f"error: {error}")


def load_baseline(directory: Path) -> dict:
    """Read the captured legacy output. Missing files are an error, never a substitute."""
    summary = json.loads(gzip.open(directory / "summary.json.gz", "rt", encoding="utf-8").read())
    hourly = json.loads(gzip.open(directory / "hourly_breakdown.json.gz", "rt", encoding="utf-8").read())
    users = [
        json.loads(line)
        for line in gzip.open(directory / "top_users.jsonl.gz", "rt", encoding="utf-8").read().splitlines()
        if line.strip()
    ]
    exit_code = (directory / "exit_code.txt").read_text(encoding="utf-8").strip().removeprefix("exit=")

    # Optional second capture: the legacy script run with its sources taken away, which is
    # the defect this conversion retires. Absent, check 3 says so instead of implying it.
    zero_event_dir = directory / "zero_event"
    zero_event = None
    if (zero_event_dir / "stdout.txt").exists():
        zero_event = {
            "stdout": (zero_event_dir / "stdout.txt").read_text(encoding="utf-8"),
            "exit_code": (zero_event_dir / "exit_code.txt").read_text(encoding="utf-8").strip().removeprefix("exit="),
            "dir": str(zero_event_dir),
        }

    by_hour_type: dict[tuple[str, str], int] = {}
    for hour, per_type in hourly.items():
        for event_type, count in per_type.items():
            by_hour_type[(hour, event_type)] = by_hour_type.get((hour, event_type), 0) + int(count)
    by_type: dict[str, int] = {}
    for (_hour, event_type), count in by_hour_type.items():
        by_type[event_type] = by_type.get(event_type, 0) + count

    return {
        "summary": summary,
        "total_events": int(summary["total_events"]),
        "by_hour_type": by_hour_type,
        "by_type": by_type,
        "users": {user["user_id"]: int(user["total"]) for user in users},
        "hours": sorted({hour for hour, _ in by_hour_type}),
        "exit_code": exit_code,
        "stdout": (directory / "stdout.txt").read_text(encoding="utf-8"),
        "zero_event": zero_event,
    }


def converted_facts(ns: str, catalog: str) -> dict:
    counts = {key: int(dbx.sql_scalar(query)) for key, query in pipeline.count_queries(catalog, ns).items()}
    by_type = {
        row[0]: int(row[1])
        for row in dbx.sql(
            f"SELECT event_type, sum(event_count) FROM {catalog}.gold.analytics_daily_summary "
            f"WHERE ns = '{ns}' GROUP BY event_type"
        )
    }
    by_hour_type = {
        (row[0], row[1]): int(row[2])
        for row in dbx.sql(
            f"SELECT lpad(cast(hour AS STRING), 2, '0'), event_type, sum(event_count) "
            f"FROM {catalog}.gold.analytics_daily_summary WHERE ns = '{ns}' GROUP BY 1, 2"
        )
    }
    shape = dbx.sql(
        "SELECT count(*), count(DISTINCT summary_date), count(DISTINCT hour), count(DISTINCT user_id), "
        f"count(DISTINCT document_id), count(DISTINCT file_id) FROM {catalog}.gold.analytics_daily_summary "
        f"WHERE ns = '{ns}'"
    )[0]
    rejects = {row[0]: int(row[1]) for row in dbx.sql(
        f"SELECT reject_reason, count(*) FROM {catalog}.silver.analytics_events_rejects "
        f"WHERE ns = '{ns}' GROUP BY reject_reason"
    )}
    unreasoned = int(dbx.sql_scalar(
        f"SELECT count(*) FROM {catalog}.silver.analytics_events_rejects "
        f"WHERE ns = '{ns}' AND (reject_reason IS NULL OR reject_reason = '')"
    ))
    return {
        "counts": counts,
        "by_type": by_type,
        "by_hour_type": by_hour_type,
        "gold_rows": int(shape[0]),
        "dates": int(shape[1]),
        "hours": int(shape[2]),
        "users": int(shape[3]),
        "documents": int(shape[4]),
        "files": int(shape[5]),
        "rejects": rejects,
        "rejects_without_reason": unreasoned,
    }


def gold_fingerprint(ns: str, catalog: str) -> str:
    return str(dbx.sql_scalar(
        "SELECT md5(array_join(array_sort(collect_list(concat_ws('|', cast(summary_date AS STRING), "
        "cast(hour AS STRING), coalesce(user_id, ''), coalesce(document_id, ''), coalesce(file_id, ''), "
        f"event_type, cast(event_count AS STRING)))), ';')) FROM {catalog}.gold.analytics_daily_summary "
        f"WHERE ns = '{ns}'"
    ))


def check_1(baseline: dict, converted: dict) -> Check:
    check = Check(1, "Event-count parity, zero silent drops")
    counts = converted["counts"]
    passed = check.record("total events", baseline["total_events"], counts["silver"])
    passed &= check.record(
        "silver + rejects vs bronze", counts["bronze"], counts["silver"] + counts["rejects"]
    )
    passed &= check.record("rejects without a reason", 0, converted["rejects_without_reason"])
    passed &= check.record("gold event_count sum vs silver rows", counts["silver"], counts["gold_events"])
    check.note(f"reject reasons: {converted['rejects'] or 'none'}")
    check.resolve(bool(passed))
    return check


def check_2(baseline: dict, converted: dict) -> Check:
    check = Check(2, "Aggregate parity on (summary_date, hour, user_id, document_id, event_type)")
    baseline_groups = set(baseline["by_hour_type"])
    converted_groups = set(converted["by_hour_type"])
    exact = baseline["by_hour_type"] == converted["by_hour_type"]

    check.note(LEGACY_ATTRIBUTION_NOTE)
    check.record("group count at (hour, event_type)", len(baseline_groups), len(converted_groups), must_match=False)
    check.record("distinct hours", baseline["hours"], sorted({hour for hour, _ in converted_groups}), must_match=False)
    check.record("distinct user_id count", len(baseline["users"]), converted["users"], must_match=False)
    check.record("distinct summary_date count", 0, converted["dates"], must_match=False)
    check.record("exact group equality", True, exact)
    # Reported alongside the failure, never instead of it: these are the only dimensions the
    # baseline actually carries, and they do match exactly.
    check.record("total events (dimension-free)", baseline["total_events"], sum(converted["by_type"].values()))
    check.record("per event_type totals", baseline["by_type"], converted["by_type"])
    check.resolve(exact)
    return check


def check_3(ns: str, catalog: str, baseline: dict) -> Check:
    check = Check(3, "Retry deficiency retired: a failing/empty source fails the run")
    probe_ns = f"{PROBE_NS_PREFIX}_{ns}"
    missing_table = f"{catalog}.bronze.analytics_daily_stage_missing"
    outcomes: list[bool] = []

    zero_event = baseline["zero_event"]
    if zero_event:
        quoted = [
            line.strip()
            for line in zero_event["stdout"].splitlines()
            if "giving up" in line or "No events found" in line or "Extracted 0 events" in line
        ]
        check.note(
            f"legacy behaviour being retired, captured at {zero_event['dir']} "
            f"(legacy run with its sources removed): {' / '.join(quoted)} -> exit {zero_event['exit_code']}"
        )
        outcomes.append(
            check.record("legacy exit code on a zero-event extract", "0", zero_event["exit_code"], must_match=False)
        )
    else:
        check.note(
            "legacy zero-event behaviour not captured in this run; the tier-1 baseline run had its "
            "fixtures in place and succeeded, so the defect is quoted from etl/scripts/analytics_daily.py "
            "(consecutive_errors >= 3 -> 'Too many SQS failures, giving up', then len(all_events) == 0 -> "
            "'No events found, exiting' + sys.exit(0)) rather than from a captured run"
        )

    # (a) unreachable source: bounded retries, then the run raises.
    try:
        runner.run(probe_ns, catalog, "s3", apply_ddl=False, source_table=missing_table)
        check.note(f"unreachable source ({missing_table}): run SUCCEEDED -- deficiency NOT retired")
        outcomes.append(False)
    except pipeline.ZeroEventExtract as exc:
        check.note(f"unreachable source: raised ZeroEventExtract instead of the source error: {exc}")
        outcomes.append(False)
    except Exception as exc:  # noqa: BLE001 - any source failure must surface, not be swallowed
        check.note(f"unreachable source: run failed as required ({type(exc).__name__}: {str(exc)[:200]})")
        outcomes.append(True)

    # (b) reachable but empty source: no zero-event "success".
    dbx.sql(f"DELETE FROM {runner.stage_table(catalog)} WHERE ns = '{probe_ns}'")
    try:
        runner.run(probe_ns, catalog, "s3", apply_ddl=False, source_table=runner.stage_table(catalog))
        check.note("empty source: run SUCCEEDED with zero events -- deficiency NOT retired")
        outcomes.append(False)
    except pipeline.ZeroEventExtract as exc:
        check.note(f"empty source: run failed as required (ZeroEventExtract: {str(exc)[:160]})")
        outcomes.append(True)
    except Exception as exc:  # noqa: BLE001
        check.note(f"empty source: run failed with {type(exc).__name__}: {str(exc)[:200]}")
        outcomes.append(True)

    probe_gold = int(dbx.sql_scalar(
        f"SELECT count(*) FROM {catalog}.gold.analytics_daily_summary WHERE ns = '{probe_ns}'"
    ))
    outcomes.append(check.record(f"gold rows written for the failed probe ns {probe_ns}", 0, probe_gold))
    check.resolve(all(outcomes))
    return check


def check_4(ns: str, catalog: str, converted: dict) -> Check:
    check = Check(4, "Idempotency: a re-run replaces, never appends")
    before = dict(converted["counts"])
    fingerprint_before = gold_fingerprint(ns, catalog)
    runner.run(ns, catalog, "s3", apply_ddl=False, source_table=runner.stage_table(catalog))
    after = {key: int(dbx.sql_scalar(query)) for key, query in pipeline.count_queries(catalog, ns).items()}
    passed = check.record("counts", before, after)
    passed &= check.record("gold fingerprint", fingerprint_before, gold_fingerprint(ns, catalog))
    check.resolve(bool(passed))
    return check


def check_5(baseline_dir: Path, baseline: dict) -> Check:
    check = Check(5, "Baseline provenance stated verbatim")
    check.note(f"report line 1: {BASELINE_TIER!r}")
    check.note(f"captured legacy run: {baseline_dir} (exit {baseline['exit_code']})")
    check.resolve(True)
    return check


def render(checks: list[Check], ns: str, catalog: str, baseline_dir: Path, converted: dict, transport: str) -> str:
    verdict = "green" if all(c.status == "PASS" for c in checks) else (
        "blocked" if any(c.status == "BLOCKED" for c in checks) else "partial"
    )
    lines = [
        BASELINE_TIER,
        "",
        f"# Recon: `analytics_daily.py` -> `ow_tp_analytics_daily` (ns=`{ns}`, catalog=`{catalog}`)",
        "",
        f"- verdict: **{verdict}**",
        f"- baseline: captured legacy run at `{baseline_dir}` (tier 1, real legacy output)",
        f"- converted output: `{catalog}.bronze.analytics_events_raw` / `{catalog}.silver.analytics_events`"
        f" / `{catalog}.silver.analytics_events_rejects` / `{catalog}.gold.analytics_daily_summary`",
        f"- extract transport used for this evidence: {transport}",
        f"- converted counts: {converted['counts']}",
        "",
    ]
    for check in checks:
        lines += [f"## {check.number}. {check.title} — **{check.status}**", "", "```text"]
        lines += check.lines
        lines += ["```", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--catalog", default=pipeline.DEFAULT_CATALOG)
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    parser.add_argument("--write", action="store_true", help=f"write the report to {REPORT_PATH}")
    parser.add_argument(
        "--transport",
        default="SQL staging table `bronze.analytics_daily_stage` (the workspace PAT lacks the `files` scope "
        "needed to write the landing volume; the extract statement is otherwise identical)",
    )
    args = parser.parse_args(argv)

    baseline = load_baseline(args.baseline_dir)
    converted = converted_facts(args.ns, args.catalog)

    checks = [
        check_1(baseline, converted),
        check_2(baseline, converted),
        check_3(args.ns, args.catalog, baseline),
        check_4(args.ns, args.catalog, converted),
        check_5(args.baseline_dir, baseline),
    ]
    report = render(checks, args.ns, args.catalog, args.baseline_dir, converted, args.transport)
    print(report)
    if args.write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(f"wrote {REPORT_PATH}", file=sys.stderr)
    return 0 if all(check.status == "PASS" for check in checks) else 1


if __name__ == "__main__":
    sys.exit(main())

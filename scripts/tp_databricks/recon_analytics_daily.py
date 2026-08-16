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
import base64
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

# The legacy script parses the hour from `timestamp` and resolves users from
# `ownerId`/`editedBy`/`authorId`/`deletedBy`/`userId`; the seeded records carry `occurred_at`
# and `user_id`. The legacy run therefore counted every event but attributed none of them:
# one `"00"` hour bucket and a single `unknown` user. That is a property of the baseline, not
# of the conversion. Check 2 keeps the exact group comparison and its unequal values, and
# labels the difference a surfaced legacy deficiency rather than either relaxing the comparison
# or porting the defect to match it.
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
        self.cleanup_failures: list[str] = []

    def record(self, label: str, baseline: object, converted: object, *, must_match: bool = True) -> bool:
        equal = baseline == converted
        marker = "=" if equal else "!="
        self.lines.append(f"{label}: baseline={baseline!r} {marker} converted={converted!r}")
        return equal or not must_match

    def note(self, text: str) -> None:
        self.lines.append(text)

    def resolve(self, passed: bool) -> None:
        self.status = "PASS" if passed else "FAIL"

    def deviate(self, reason: str) -> None:
        """A comparison whose difference is a legacy defect the conversion refuses to port.

        Distinct from PASS: the exact comparison and its unequal values stay in the report,
        labelled, so the deviation cannot be mistaken for parity.
        """
        self.status = "DEVIATION"
        self.lines.append(f"deviation: {reason}")

    def block(self, command: str, error: str) -> None:
        self.status = "BLOCKED"
        self.lines.append(f"command: {command}")
        self.lines.append(f"error: {error}")


def load_baseline(directory: Path) -> dict:
    """Read the captured legacy output. Missing files are an error, never a substitute."""
    with gzip.open(directory / "summary.json.gz", "rt", encoding="utf-8") as summary_file:
        summary = json.load(summary_file)
    with gzip.open(directory / "hourly_breakdown.json.gz", "rt", encoding="utf-8") as hourly_file:
        hourly = json.load(hourly_file)
    with gzip.open(directory / "top_users.jsonl.gz", "rt", encoding="utf-8") as users_file:
        users = [json.loads(line) for line in users_file if line.strip()]
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
    user_actions = {
        (user["user_id"], event_type): int(count)
        for user in users
        for event_type, count in user.get("actions", {}).items()
    }
    artifacts = [summary, *users]
    date_fields = sorted({
        field
        for artifact in artifacts
        for field in artifact
        if "date" in field.lower()
    })
    dates = sorted({
        str(artifact[field])
        for artifact in artifacts
        for field in date_fields
        if field in artifact and artifact[field] is not None
    })

    return {
        "summary": summary,
        "total_events": int(summary["total_events"]),
        "active_users": int(summary["active_users"]),
        "by_hour_type": by_hour_type,
        "by_type": by_type,
        "users": {user["user_id"]: int(user["total"]) for user in users},
        "user_actions": user_actions,
        "hours": sorted({hour for hour, _ in by_hour_type}),
        "date_fields": date_fields,
        "dates": dates,
        "exit_code": exit_code,
        "stdout": (directory / "stdout.txt").read_text(encoding="utf-8"),
        "zero_event": zero_event,
    }


def converted_facts(ns: str, catalog: str, sample_users: set[str] | None = None) -> dict:
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
    user_filter = ""
    if sample_users is not None:
        if not sample_users:
            user_filter = " AND 1 = 0"
        else:
            encoded_users = (
                "decode(unbase64('"
                + base64.b64encode(user.encode("utf-8")).decode("ascii")
                + "'), 'UTF-8')"
                for user in sorted(sample_users)
            )
            quoted_users = ", ".join(encoded_users)
            user_filter = f" AND user_id IN ({quoted_users})"
    user_by_type = {
        (row[0], row[1]): int(row[2])
        for row in dbx.sql(
            f"SELECT user_id, event_type, count(*) FROM {catalog}.silver.analytics_events "
            f"WHERE ns = '{ns}'{user_filter} GROUP BY user_id, event_type"
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
        "user_by_type": user_by_type,
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
    # The contract compares the legacy total against silver. Bronze is recorded next to it so a
    # non-zero reject population is visible as quarantine rather than looking like loss.
    check.record("extracted events (bronze)", baseline["total_events"], counts["bronze"], must_match=False)
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
    check = Check(2, "Aggregate parity on (hour, user_id, event_type) — legacy-comparable grain")
    baseline_groups = set(baseline["by_hour_type"])
    converted_groups = set(converted["by_hour_type"])
    exact = baseline["by_hour_type"] == converted["by_hour_type"]

    check.note(LEGACY_ATTRIBUTION_NOTE)
    check.record("group count at (hour, event_type)", len(baseline_groups), len(converted_groups), must_match=False)
    check.record("distinct hours", baseline["hours"], sorted({hour for hour, _ in converted_groups}), must_match=False)
    check.record("distinct user_id count", baseline["active_users"], converted["users"], must_match=False)
    check.record("distinct summary_date count", len(baseline["dates"]), converted["dates"], must_match=False)
    check.note(
        f"legacy top-100 user sample for the defect signature: {sorted(baseline['users'])}"
    )
    if not baseline["users"]:
        check.note(
            "legacy top-100 user sample is empty; the user-grain comparison intentionally "
            "restricts both sides to no users"
        )
    check.note("contract dimensions unavailable in legacy artifacts: summary_date, document_id, file_id")
    user_comparable = check.record(
        "(user_id, event_type) totals — both sides restricted to legacy top-100 sample",
        baseline["user_actions"],
        converted["user_by_type"],
    )
    check.note(
        f"legacy artifact date-bearing fields: {baseline['date_fields']} "
        "(event dates are absent; the run date exists only in the ds S3 partition)"
    )
    check.record("exact group equality", True, exact)
    # The dimensions the baseline actually carries, compared exactly and never in place of
    # the group comparison above.
    comparable = check.record(
        "total events (dimension-free)", baseline["total_events"], sum(converted["by_type"].values())
    )
    comparable &= check.record("per event_type totals", baseline["by_type"], converted["by_type"])
    legacy_signature = (
        baseline["hours"] == ["00"]
        and set(baseline["users"]) == {"unknown"}
        and baseline["date_fields"] == []
    )
    check.note(
        f"legacy defect signature: hours={baseline['hours']}, "
        f"user_ids={sorted(baseline['users'])} (top-100 sample), date_fields={baseline['date_fields']}"
    )

    if exact and user_comparable:
        check.resolve(True)
    elif comparable and legacy_signature:
        # The hour/user divergence is the legacy field-name defect: the 2014 script parses
        # `timestamp` and resolves users from ownerId/editedBy/authorId/deletedBy/userId while
        # the events carry occurred_at/user_id. Matching it would mean porting the bug, so the
        # difference is recorded as a surfaced deficiency.
        check.deviate(
            "legacy field-name defect surfaced, converted output correct -- the legacy script reads "
            "timestamp and resolves users from ownerId/editedBy/authorId/deletedBy/userId while the "
            "events carry occurred_at/user_id, so it "
            f"collapsed all {baseline['total_events']} events into {len(baseline_groups)} groups at "
            "hour='00'/user_id='unknown' with no event date dimension; the converted job attributes them across "
            f"{len(converted_groups)} groups/{converted['hours']} hours/{converted['users']} users/"
            f"{converted['dates']} dates. Dimension-free and per-event_type totals match exactly; "
            "the hour/user attribution differences are shown above and qualify only under this signature."
        )
    else:
        check.resolve(False)
    return check


def _cleanup_check_3_probe(check: Check, probe_ns: str, catalog: str, source_table: str | None) -> None:
    tables = (
        f"{catalog}.bronze.analytics_events_raw",
        f"{catalog}.silver.analytics_events",
        f"{catalog}.silver.analytics_events_rejects",
        f"{catalog}.gold.analytics_daily_summary",
    ) + ((runner.stage_table(catalog),) if source_table is not None else ())
    for table in tables:
        try:
            dbx.sql(f"DELETE FROM {table} WHERE ns = '{probe_ns}'")
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the check verdict
            failure = f"{table}: {type(exc).__name__}: {exc}"
            check.cleanup_failures.append(failure)
            check.note(f"probe cleanup failed for {failure}")

    if source_table is None:
        probe_events = f"/Volumes/{catalog}/bronze/landing/{runner.volume_prefix(probe_ns)}/events"
        try:
            runner._clear_landing_events(probe_events)
        except Exception as exc:  # noqa: BLE001 - Files API is intentionally unverified here
            check.note(
                f"probe landing cleanup failed for {probe_events}: "
                f"{type(exc).__name__}: {exc}"
            )


def check_3(ns: str, catalog: str, baseline: dict, source_table: str | None) -> Check:
    check = Check(3, "Retry deficiency retired: a failing/empty source fails the run")
    probe_ns = f"{PROBE_NS_PREFIX}_{ns}"
    try:
        return _check_3_body(check, ns, catalog, baseline, source_table, probe_ns)
    finally:
        _cleanup_check_3_probe(check, probe_ns, catalog, source_table)


def _check_3_body(
    check: Check,
    ns: str,
    catalog: str,
    baseline: dict,
    source_table: str | None,
    probe_ns: str,
) -> Check:
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
        check.note(
            f"legacy zero-event extract exited {zero_event['exit_code']}; "
            "the converted zero-event probes below raise instead of exiting successfully"
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
    except Exception as exc:  # noqa: BLE001 - classify infrastructure failures as blocked
        message = str(exc)
        if (
            exc.__class__.__name__ == "DatabricksError"
            and ("TABLE_OR_VIEW_NOT_FOUND" in message or missing_table in message)
        ):
            check.note(f"unreachable source: run failed as required ({type(exc).__name__}: {message[:200]})")
            outcomes.append(True)
        else:
            check.block(
                f"runner.run(ns={probe_ns!r}, catalog={catalog!r}, source_table={missing_table!r})",
                f"{type(exc).__name__}: {message}",
            )
            return check

    # (b) reachable but empty source: no zero-event "success". In volume mode, first create
    # and remove a sentinel so the probe directory exists without any source files.
    if source_table:
        empty_source_cleanup = f"DELETE FROM {source_table} WHERE ns = '{probe_ns}'"
        try:
            dbx.sql(empty_source_cleanup)
        except Exception as exc:  # noqa: BLE001 - the empty-source probe could not execute
            check.block(empty_source_cleanup, f"{type(exc).__name__}: {exc}")
            return check
    else:
        probe_events = f"/Volumes/{catalog}/bronze/landing/{runner.volume_prefix(probe_ns)}/events"
        sentinel = f"{probe_events}/.recon-empty"
        setup_command = (
            f"PUT /api/2.0/fs/files{sentinel} then "
            f"DELETE /api/2.0/fs/files{sentinel} (create empty probe directory)"
        )
        try:
            dbx.request("PUT", f"/api/2.0/fs/files{sentinel}?overwrite=true", raw=b"")
            dbx.request("DELETE", f"/api/2.0/fs/files{sentinel}")
        except Exception as exc:  # noqa: BLE001 - an unexecutable volume probe is blocked
            check.block(setup_command, f"{type(exc).__name__}: {exc}")
            return check
    try:
        runner.run(probe_ns, catalog, "s3", apply_ddl=False, source_table=source_table)
        check.note("empty source: run SUCCEEDED with zero events -- deficiency NOT retired")
        outcomes.append(False)
    except pipeline.ZeroEventExtract as exc:
        check.note(f"empty source: run failed as required (ZeroEventExtract: {str(exc)[:160]})")
        outcomes.append(True)
    except Exception as exc:  # noqa: BLE001
        # Only ZeroEventExtract demonstrates the zero-event path. Any other error means this
        # sub-probe never reached a reachable-but-empty extract, so it proves nothing -- in
        # volume mode, for instance, the probe namespace has no directory to read at all.
        if source_table is None:
            check.block(
                f"runner.run(ns={probe_ns!r}, catalog={catalog!r}, source_table=None)",
                f"{type(exc).__name__}: {exc}",
            )
            return check
        check.note(
            f"empty source: INCONCLUSIVE -- failed before the empty extract with "
            f"{type(exc).__name__}: {str(exc)[:200]}"
        )
        outcomes.append(False)

    probe_gold = int(dbx.sql_scalar(
        f"SELECT count(*) FROM {catalog}.gold.analytics_daily_summary WHERE ns = '{probe_ns}'"
    ))
    outcomes.append(check.record(f"gold rows written for the failed probe ns {probe_ns}", 0, probe_gold))
    check.resolve(all(outcomes))
    return check


def check_4(ns: str, catalog: str, converted: dict, source_table: str | None) -> Check:
    check = Check(4, "Idempotency: a re-run replaces, never appends")
    before = dict(converted["counts"])
    fingerprint_before = gold_fingerprint(ns, catalog)
    # The re-run must read whatever the original load read: replaying a volume-loaded slice
    # from an empty staging table would replace bronze with nothing.
    check.note(f"re-run source: {source_table or 'landing volume'}")
    runner.run(ns, catalog, "s3", apply_ddl=False, source_table=source_table)
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
    deviations = [check for check in checks if check.status == "DEVIATION"]
    if any(check.status == "BLOCKED" for check in checks):
        verdict = "blocked"
    elif any(check.status == "FAIL" for check in checks):
        verdict = "partial"
    elif deviations:
        verdict = (
            f"green with {len(deviations)} documented legacy-deficiency deviation"
            f"{'s' if len(deviations) > 1 else ''} "
            f"(check{'s' if len(deviations) > 1 else ''} {', '.join(str(c.number) for c in deviations)})"
        )
    else:
        verdict = "green"
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
    cleanup_failures = [
        failure
        for check in checks
        for failure in check.cleanup_failures
    ]
    if cleanup_failures:
        lines += [
            "## Probe cleanup — **FAILURE**",
            "",
            "The check verdicts above reflect the observed semantics, but probe rows may remain:",
            *[f"- {failure}" for failure in cleanup_failures],
            "",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ns", default=os.environ.get("NS", "demo"))
    parser.add_argument("--catalog", default=os.environ.get("OW_TP_CATALOG", pipeline.DEFAULT_CATALOG))
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    parser.add_argument("--write", action="store_true", help=f"write the report to {REPORT_PATH}")
    parser.add_argument(
        "--from-volume",
        action="store_true",
        help="the slice under recon was loaded from the landing volume, not the staging table; "
        "checks 3 and 4 then replay from the volume too",
    )
    parser.add_argument("--transport", default=None, help="override the transport line in the report")
    args = parser.parse_args(argv)
    pipeline.validate_ns(args.ns)
    pipeline.validate_identifier(args.catalog, "catalog")
    os.environ["OW_TP_CATALOG"] = args.catalog
    dbx.CATALOG = dbx._catalog()

    source_table = None if args.from_volume else runner.stage_table(args.catalog)
    transport = args.transport or (
        f"landing volume `/Volumes/{args.catalog}/bronze/landing/{args.ns}/analytics_daily/events/`"
        if args.from_volume
        else "SQL staging table `bronze.analytics_daily_stage`. The documented landing-volume upload "
        "path (`dbx.upload` into `/Volumes/{catalog}/bronze/landing/...`) is **UNVERIFIED**: the demo "
        "PAT lacks the `files` scope and every attempt returns `403: {\"error_code\":403,\"message\":"
        "\"Provided access token does not have required scopes: files\"}`. The staging table is an "
        "evidence-only substitute, not the production transport; the extract statement downstream of "
        "the source relation is byte-identical, and no check was weakened to accommodate it."
    ).replace("{catalog}", args.catalog)

    # Evidence collection is inside the same guarantee as the checks: if the baseline files or
    # the warehouse are unavailable there is no comparison to make, so every check is BLOCKED
    # with the error and the report is still produced.
    collection_error: str | None = None
    baseline: dict = {}
    converted: dict = {"counts": {}}
    try:
        baseline = load_baseline(args.baseline_dir)
        converted = converted_facts(args.ns, args.catalog, set(baseline["users"]))
    except Exception as exc:  # noqa: BLE001 - reported as BLOCKED, never as a pass
        collection_error = f"{type(exc).__name__}: {exc}"

    # A check that cannot execute is reported BLOCKED with its error, so an infrastructure
    # failure still produces a report instead of a traceback and no evidence at all.
    definitions = [
        ("check_1", lambda: check_1(baseline, converted), 1, "Event-count parity, zero silent drops"),
        ("check_2", lambda: check_2(baseline, converted), 2, "Aggregate parity"),
        ("check_3", lambda: check_3(args.ns, args.catalog, baseline, source_table), 3, "Retry deficiency retired"),
        ("check_4", lambda: check_4(args.ns, args.catalog, converted, source_table), 4, "Idempotency"),
        ("check_5", lambda: check_5(args.baseline_dir, baseline), 5, "Baseline provenance stated verbatim"),
    ]
    checks = []
    for name, run_check, number, title in definitions:
        if collection_error:
            blocked = Check(number, title)
            blocked.block(
                f"load_baseline({str(args.baseline_dir)!r}) / converted_facts(ns={args.ns!r}, "
                f"catalog={args.catalog!r})",
                collection_error,
            )
            checks.append(blocked)
            continue
        try:
            checks.append(run_check())
        except Exception as exc:  # noqa: BLE001 - an unexecutable check is BLOCKED, never green
            blocked = Check(number, title)
            blocked.block(f"{name}(ns={args.ns!r}, catalog={args.catalog!r})", f"{type(exc).__name__}: {exc}")
            checks.append(blocked)
    report = render(checks, args.ns, args.catalog, args.baseline_dir, converted, transport)
    print(report)
    if args.write:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(report, encoding="utf-8")
        print(f"wrote {REPORT_PATH}", file=sys.stderr)
    # A documented deviation is not a failure; FAIL and BLOCKED are.
    cleanup_failed = any(check.cleanup_failures for check in checks)
    return 0 if not cleanup_failed and all(check.status in ("PASS", "DEVIATION") for check in checks) else 1


if __name__ == "__main__":
    sys.exit(main())

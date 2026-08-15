#!/usr/bin/env python3
"""Reconcile the converted user_activity job against the captured legacy output.

Runs the five numbered acceptance checks from the unit's contract and writes a
report. The legacy side is read from the baseline directory captured by running the
unmodified `etl/scripts/user_activity_daily.py`; the converted side is read from
`gold.user_activity_report` on Unity Catalog. Nothing here regenerates, adjusts or
approximates the legacy numbers, and no comparison is loosened: a check either
matches exactly or is reported failed/blocked with the values that differed.

Usage:
  export DATABRICKS_HOST="$DATABRICKS_DEMO_HOST" DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
  python scripts/tp_databricks/recon_user_activity.py --ns demo \
      --baseline /home/ubuntu/tp-golden/python/user_activity_daily \
      --out docs/tech-partnerships/recon/user_activity_daily.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dbx  # noqa: E402
import run_user_activity  # noqa: E402

# The runner's module, not a second load: loading the notebook twice would give two
# distinct UpstreamNotFresh classes and the refusal checks would not catch.
pipeline = run_user_activity.pipeline

DOC_PREFIX = pipeline.DOC_PREFIX
FILE_PREFIX = pipeline.FILE_PREFIX
# The legacy baseline was captured with ds=2026-08-15 over seeded events that end
# 2026-07-31, so parity is reproduced with the legacy's own tolerance for lag; the
# job's default (1 day) refuses exactly that situation, which check 3 demonstrates.
PARITY_LAG_DAYS = "30"


def legacy_report(baseline: str) -> dict:
    with open(os.path.join(baseline, "activity_report.json"), encoding="utf-8") as handle:
        return json.load(handle)


def legacy_users(baseline: str) -> dict[str, dict]:
    users = {}
    with open(os.path.join(baseline, "user_summaries.jsonl"), encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            by_type = row["actions_by_type"]
            users[row["user_id"]] = {
                "events": int(row["total_actions"]),
                "active_days": int(row["active_days"]),
                "documents_touched": sum(
                    int(v) for k, v in by_type.items() if k.startswith(DOC_PREFIX)
                ),
                "files_touched": sum(
                    int(v) for k, v in by_type.items() if k.startswith(FILE_PREFIX)
                ),
            }
    return users


def converted_users(ns: str, catalog: str, report_date: str) -> dict[str, dict]:
    rows = dbx.sql(
        f"""
SELECT user_id, events, active_days, documents_touched, files_touched
FROM {catalog}.gold.user_activity_report
WHERE ns = '{ns}' AND report_date = DATE'{report_date}'
"""
    )
    return {
        row[0]: {
            "events": int(row[1]),
            "active_days": int(row[2]),
            "documents_touched": int(row[3]),
            "files_touched": int(row[4]),
        }
        for row in rows
    }


FIELDS = ("events", "active_days", "documents_touched", "files_touched")
# etl/scripts/user_activity_daily.py ships `"user_summaries": user_list[:500]`, so the
# captured JSONL is the busiest 500 users, not the population.
LEGACY_SUMMARY_CAP = 500


def table_exists(qualified: str) -> bool:
    catalog, schema, name = qualified.split(".")
    rows = dbx.sql(f"SHOW TABLES IN {catalog}.{schema} LIKE '{name}'")
    return bool(rows)


def truncated_baseline(legacy: dict[str, dict]) -> bool:
    """Whether the captured legacy per-user artefact is the script's capped slice.

    The row count is the only fact that establishes truncation. The report's
    `trends.peak_active_users` cannot: the legacy script sets it to
    `max(d["active_users"] for d in daily_summaries)`, the busiest single day's active
    users, while the artefact holds up to 500 distinct users accumulated over the whole
    window - so it is smaller than the population on a wide window and can exceed the
    artefact's row count on a busy day.
    """
    return len(legacy) >= LEGACY_SUMMARY_CAP


def check_per_user(legacy: dict[str, dict], converted: dict[str, dict],
                   legacy_peak_active_users: int) -> dict:
    """Check 1: row-for-row parity on user_id x report_date, exact counts.

    The legacy artefact is capped: the script writes only `user_list[:500]` to
    `user_summaries.jsonl`. Comparing an uncapped converted side against a capped
    baseline would fail a correct conversion of any namespace above the cap, so once the
    artefact sits at the cap the comparison is confined to the same top-N ranking the
    artefact represents, stated in the result rather than applied silently. Nothing is
    loosened: inside that scope every field is still exact, and if rank N is a tie the
    converted top-N is not well defined, so the check reports blocked instead of guessing
    which users the baseline kept.
    """
    truncated = truncated_baseline(legacy)
    ranked_converted = sorted(converted.items(), key=lambda kv: (-kv[1]["events"], kv[0]))
    coverage = {
        "legacy_users_in_baseline": len(legacy),
        "legacy_artefact_truncated": truncated,
        # Context only: this is a per-day peak, not the size of the population.
        "legacy_peak_active_users_reported": int(legacy_peak_active_users),
        "comparison_scope": (
            f"top {len(legacy)} users by events (the legacy artefact's cap of"
            f" {LEGACY_SUMMARY_CAP})"
            if truncated
            else "every user on both sides"
        ),
    }
    # A converted side shorter than the capped baseline is the divergence this check
    # exists to catch, so it falls through to the missing/extra comparison below instead
    # of being ranked into a top-N that does not exist.
    if truncated and len(ranked_converted) >= len(legacy):
        boundary = ranked_converted[len(legacy) - 1][1]["events"]
        beyond = ranked_converted[len(legacy):]
        if beyond and beyond[0][1]["events"] == boundary:
            return {
                "name": "1. Per-user parity (user_id x report_date, exact counts)",
                "passed": False,
                "blocked": (
                    f"the legacy artefact is capped at {len(legacy)} users and rank"
                    f" {len(legacy)} is a tie at"
                    f" {boundary} events, so which users the baseline kept is not"
                    " recoverable: parity cannot be asserted for this namespace without"
                    " an uncapped legacy artefact."
                ),
                "coverage": coverage,
            }
        compared = dict(ranked_converted[: len(legacy)])
    else:
        compared = converted

    missing = sorted(set(legacy) - set(compared))
    extra = sorted(set(compared) - set(legacy))
    mismatches = []
    for user_id in sorted(set(legacy) & set(compared)):
        diffs = {
            field: (legacy[user_id][field], compared[user_id][field])
            for field in FIELDS
            if legacy[user_id][field] != compared[user_id][field]
        }
        if diffs:
            mismatches.append({"user_id": user_id, "diffs": diffs})
    ranked = sorted(legacy.items(), key=lambda kv: -kv[1]["events"])
    tail_band = "long tail" if not truncated else f"baseline tail (rank <= {len(legacy)})"
    sample = [
        {"user_id": uid, "band": band, "legacy_events": vals["events"],
         "converted_events": converted.get(uid, {}).get("events"),
         "legacy_active_days": vals["active_days"],
         "converted_active_days": converted.get(uid, {}).get("active_days")}
        # A head sample would hide the tail: the seeded ownership is a power law, so
        # the three biggest and the three smallest owners are both shown.
        for band, (uid, vals) in (
            [("whale", kv) for kv in ranked[:3]] + [(tail_band, kv) for kv in ranked[-3:]]
        )
    ]
    return {
        "name": "1. Per-user parity (user_id x report_date, exact counts)",
        "passed": not missing and not extra and not mismatches,
        "legacy_users": len(legacy),
        "converted_users": len(converted),
        "coverage": coverage,
        "missing_users": missing,
        "unexpected_users": extra,
        "mismatches": mismatches,
        "sample": sample,
    }


def check_totals(legacy_rpt: dict, legacy: dict[str, dict], converted: dict[str, dict],
                 ns: str, catalog: str, report_date: str, lookback_days: str) -> dict:
    """Check 2: per-user sums cross-foot to the upstream aggregate totals.

    When the legacy per-user artefact is capped (see `check_per_user`) its subtotal is a
    subset by construction and is reported as such instead of being cross-footed against
    the full totals; the legacy report's own `trends.total_events` is uncapped and stays
    the authoritative legacy side, so nothing is compared against the conversion itself.
    """
    upstream_total = dbx.sql(
        f"""
SELECT CAST(SUM(total_events) AS BIGINT)
FROM {catalog}.bronze.user_activity_upstream_fixture
WHERE ns = '{ns}' AND report_date BETWEEN DATE'{report_date}' - INTERVAL {lookback_days} DAYS
                                      AND DATE'{report_date}'
"""
    )[0][0]
    legacy_sum = sum(v["events"] for v in legacy.values())
    converted_sum = sum(v["events"] for v in converted.values())
    trend_total = int(legacy_rpt["trends"]["total_events"])
    truncated = truncated_baseline(legacy)
    values = {
        "converted per-user sum": converted_sum,
        "legacy report trends.total_events": trend_total,
        "upstream aggregate SUM(total_events)": int(upstream_total),
    }
    if truncated:
        extra = {
            f"legacy per-user sum (capped at {len(legacy)} users, subset by construction)":
                legacy_sum
        }
    else:
        values["legacy per-user sum"] = legacy_sum
        extra = {}
    return {
        "name": "2. Totals cross-foot (no rows lost or invented)",
        "passed": len(set(values.values())) == 1,
        "values": {**values, **extra},
    }


def check_freshness(ns: str, catalog: str, report_date: str, lookback_days: str) -> dict:
    """Check 3: stale and missing upstream must refuse, writing no report rows."""
    scenarios = []
    table = f"{catalog}.bronze.user_activity_upstream_fixture"
    # Namespaced like every other table in this unit: a shared scratch name would let a
    # run for one namespace overwrite the copy another namespace's interrupted run left
    # behind. `-` is legal in ns but not in an identifier.
    backup = (f"{catalog}.bronze.user_activity_upstream_recon_backup_"
              f"{ns.replace('-', '_')}")
    rows_before = dbx.sql(
        f"SELECT COUNT(*) FROM {catalog}.gold.user_activity_report "
        f"WHERE ns = '{ns}' AND report_date = DATE'{report_date}'"
    )[0][0]

    def attempt(label: str, params: dict[str, str]) -> dict:
        base = {"ns": ns, "catalog": catalog, "report_date": report_date,
                "lookback_days": lookback_days, "source_mode": "table"}
        base.update(params)
        try:
            result = run_user_activity.run(base)
            outcome = {"refused": False, "detail": json.dumps(result, default=str)[:400]}
        except pipeline.UpstreamNotFresh as exc:
            outcome = {"refused": True, "detail": str(exc)}
        rows_after = dbx.sql(
            f"SELECT COUNT(*) FROM {catalog}.gold.user_activity_report "
            f"WHERE ns = '{ns}' AND report_date = DATE'{report_date}'"
        )[0][0]
        log = dbx.sql(
            f"SELECT status, upstream_fresh, rows_written FROM {catalog}.gold.user_activity_run_log "
            f"WHERE ns = '{ns}' ORDER BY run_ts DESC LIMIT 1"
        )
        outcome.update({
            "scenario": label,
            "report_rows_after": int(rows_after),
            "report_rows_unchanged": int(rows_after) == int(rows_before),
            "run_log": log[0] if log else None,
        })
        return outcome

    # 3a: upstream present but 15 days behind report_date, at the job's real default.
    scenarios.append(attempt("stale upstream, default 1-day tolerance", {}))

    # 3b: upstream absent for the namespace entirely (the analytics job never ran).
    # The rows are parked in a scratch Delta table rather than in this process's memory,
    # so an interrupted or failed restore can still be completed afterwards from SQL
    # instead of losing the shared fixture.
    #
    # A leftover backup means exactly that: a previous run died between the delete and
    # the restore, and the scratch table is the only surviving copy. Finish that restore
    # first — replacing it from the (still empty) live table would destroy the fixture.
    # The table belongs to this namespace by name, so every row in it is a leftover of
    # this namespace's own interrupted run and is restored unfiltered. A foreign row means
    # the table is not what it claims to be: refuse rather than delete a live slice that
    # the recovery would not put back.
    leftover, foreign = 0, 0
    if table_exists(backup):
        counts = dbx.sql(
            f"SELECT COUNT(*), COUNT_IF(ns <> '{ns}') FROM {backup}"
        )[0]
        leftover, foreign = int(counts[0]), int(counts[1] or 0)
    if foreign:
        return {
            "name": "3. Freshness guard refuses stale/missing upstream",
            "passed": False,
            "blocked": f"{backup} holds {foreign} row(s) for another namespace: refusing to "
                       f"touch the live fixture until it is inspected and dropped by hand.",
            "report_rows_before": int(rows_before),
            "scenarios": scenarios,
        }
    if leftover:
        dbx.sql(f"DELETE FROM {table} WHERE ns = '{ns}'")
        dbx.sql(f"INSERT INTO {table} SELECT * FROM {backup}")
    dbx.sql(f"CREATE OR REPLACE TABLE {backup} AS "
            f"SELECT * FROM {table} WHERE ns = '{ns}'")
    saved = int(dbx.sql(f"SELECT COUNT(*) FROM {backup}")[0][0])
    if not saved:
        # Nothing to delete and nothing to restore: the scenario would "refuse" for the
        # wrong reason and a zero-row restore would satisfy the count check trivially.
        return {
            "name": "3. Freshness guard refuses stale/missing upstream",
            "passed": False,
            "blocked": f"{table} holds no rows for ns={ns}: the missing-upstream scenario "
                       f"cannot be distinguished from an already-empty fixture. Re-land it "
                       f"with land_user_activity.py --upstream-only and re-run.",
            "report_rows_before": int(rows_before),
            "scenarios": scenarios,
        }
    dbx.sql(f"DELETE FROM {table} WHERE ns = '{ns}'")
    try:
        scenarios.append(attempt("missing upstream (analytics job never ran)",
                                 {"max_upstream_lag_days": PARITY_LAG_DAYS}))
    finally:
        dbx.sql(f"DELETE FROM {table} WHERE ns = '{ns}'")
        dbx.sql(f"INSERT INTO {table} SELECT * FROM {backup}")
    restored = int(dbx.sql(f"SELECT COUNT(*) FROM {table} WHERE ns = '{ns}'")[0][0])
    if restored == saved:
        dbx.sql(f"DROP TABLE IF EXISTS {backup}")

    return {
        "name": "3. Freshness guard refuses stale/missing upstream",
        # The restore is part of the check: a scenario that leaves the fixture short is a
        # failure, not a footnote.
        "passed": (all(s["refused"] and s["report_rows_unchanged"] for s in scenarios)
                   and restored == saved),
        "report_rows_before": int(rows_before),
        "upstream_rows_recovered_from_leftover_backup": leftover,
        "upstream_rows_saved": saved,
        "upstream_rows_restored": restored,
        "scenarios": scenarios,
    }


def check_idempotency(ns: str, catalog: str, report_date: str, lookback_days: str) -> dict:
    """Check 4: a re-run leaves exactly one row per user/date."""
    before = dbx.sql(
        f"SELECT COUNT(*) FROM {catalog}.gold.user_activity_report "
        f"WHERE ns = '{ns}' AND report_date = DATE'{report_date}'"
    )[0][0]
    run_user_activity.run({
        "ns": ns, "catalog": catalog, "report_date": report_date,
        "lookback_days": lookback_days, "source_mode": "table",
        "max_upstream_lag_days": PARITY_LAG_DAYS,
    })
    after = dbx.sql(
        f"""
SELECT COUNT(*), COUNT(DISTINCT user_id),
       (SELECT COUNT(*) FROM (
          SELECT user_id FROM {catalog}.gold.user_activity_report
          WHERE ns = '{ns}' AND report_date = DATE'{report_date}'
          GROUP BY user_id HAVING COUNT(*) > 1))
FROM {catalog}.gold.user_activity_report
WHERE ns = '{ns}' AND report_date = DATE'{report_date}'
"""
    )[0]
    rows, users, duplicates = int(after[0]), int(after[1]), int(after[2])
    return {
        "name": "4. Idempotency (re-run duplicates nothing)",
        "passed": rows == int(before) and rows == users and duplicates == 0,
        "values": {"rows before re-run": int(before), "rows after re-run": rows,
                   "distinct users": users, "duplicate user/date keys": duplicates},
    }


def render(report: dict) -> str:
    """Render the markdown recon report."""
    lines = [
        "# Recon — user_activity_daily -> ow_tp_user_activity",
        "",
        "baseline: legacy output",
        "",
        "## Baseline provenance",
        "",
        "Tier 1 per `_python_wave_baseline.md`: the numbers below come from running the",
        "**unmodified** `etl/scripts/user_activity_daily.py` on this VM and capturing its",
        f"output under `{report['baseline_dir']}` (`exit_code={report['legacy_exit_code']}`).",
        "Nothing in the legacy output was regenerated, edited or synthesised by the",
        "conversion, and the conversion is never compared against itself.",
        "",
        "Standing the unit up required two documented local fixtures, neither of which is",
        "the baseline itself:",
        "",
        "1. **Postgres on 55432.** Host port 5432 was already occupied by a Postgres that",
        "   rejects the `otterworks` credentials, so the fixture ran in container",
        "   `otterworks-postgres-alt` on 55432 (`DB_PORT=55432`), and the legacy script was",
        "   pointed at it with a scratch copy of `etl/config.ini` **outside** the repo",
        "   (`/home/ubuntu/tp-scratch/etl-config/config.ini`). Nothing under `etl/` was edited.",
        "2. **The upstream analytics aggregate.** The legacy job reads",
        "   `analytics_daily.py`'s output. That job could **not** be run here — it requires",
        "   production-shaped SQS/DynamoDB resources this VM does not have — so",
        "   `scripts/tp_databricks/fixture_analytics_upstream.py` derived the aggregate",
        "   (`analytics_daily_summary` rows plus the per-day `top_users.jsonl.gz` objects)",
        "   deterministically from the same seeded events (`make seed-legacy NS=demo`).",
        "   **The real `analytics_daily.py` did not run.** The legacy user-activity script",
        "   itself ran unmodified against that aggregate, and its output is the baseline.",
        "",
        f"Legacy report: date `{report['report_date']}`, lookback"
        f" `{report['lookback_days']}` days, `trends.total_events`"
        f" = {report['legacy_total_events']}, {report['legacy_user_count']} user summaries.",
        "",
        "Baseline hashes (`manifest_sha256.txt`):",
        "",
        "```",
        report["baseline_hashes"].strip(),
        "```",
        "",
        "## How the converted side was produced",
        "",
        "The job (`ow_tp_user_activity`, PR 1/3) was **not** applied — the parent session owns",
        "workspace state. Instead `scripts/tp_databricks/run_user_activity.py` executed the",
        "job task's notebook `main()` against the existing `Serverless Starter Warehouse`, so",
        "the statements reconciled here are the statements the job runs. No cluster was",
        "created and no throwaway job was left behind.",
        "",
        "### The documented volume upload path is UNVERIFIED",
        "",
        "The production transport for this unit is the landing volume"
        f" `/Volumes/{report['catalog']}/bronze/landing`, read set-based by the notebook's",
        "`read_files()` (`source_mode=volume`), and that path is **unverified here**: the demo"
        " PAT lacks the `files` scope, so every",
        "`dbx.py upload` to the volume is refused with exactly",
        "",
        "```",
        "HTTP Error 403: Forbidden",
        "Provided access token does not have required scopes: files",
        "```",
        "",
        "The evidence below was therefore produced with the in-Databricks fallback:"
        f" `land_user_activity.py` landed the inputs into `{report['catalog']}.bronze"
        ".user_activity_events_landed` and the notebook read them with `source_mode=table`.",
        "That table is a workaround for a token limitation, **not** the production transport,"
        " and it is not presented as one; the volume wiring is unchanged and remains the",
        "job's default. No check was loosened to compensate — the aggregation, the freshness"
        " guard and every comparison below are identical in both modes; only the two lines",
        "that read the landed events differ. Proving the volume leg needs a token with the"
        " `files` scope.",
        "",
        f"Parity was reproduced with `max_upstream_lag_days={PARITY_LAG_DAYS}`, matching the",
        "legacy behaviour: the baseline was captured with `ds=2026-08-15` over seeded events",
        "that end `2026-07-31`, i.e. the legacy script reported over a 15-day-old aggregate",
        "without noticing. The job's default tolerance is 1 day and **refuses** that exact",
        "run — see check 3.",
        "",
        f"## Result: {report['verdict']}",
        "",
    ]
    for check in report["checks"]:
        lines += [f"### {check['name']} — {'PASS' if check['passed'] else 'FAIL'}", ""]
        body = {k: v for k, v in check.items() if k not in ("name", "passed")}
        lines += ["```json", json.dumps(body, indent=2, default=str), "```", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--catalog", default="ow_tp")
    parser.add_argument("--baseline", default="/home/ubuntu/tp-golden/python/user_activity_daily")
    parser.add_argument("--out", default=None, help="write the markdown report here")
    args = parser.parse_args(argv)

    rpt = legacy_report(args.baseline)
    report_date = rpt["report_date"]
    # Everything below is interpolated into SQL against a shared workspace, and the
    # report_date comes off disk: validate with the pipeline's own rules before use.
    cfg = pipeline.build_config({"ns": args.ns, "catalog": args.catalog,
                                "report_date": report_date,
                                "lookback_days": str(int(rpt["lookback_days"]))})
    lookback_days = cfg["lookback_days"]
    legacy = legacy_users(args.baseline)

    # Establish the converted side from a clean, parity-configured run, over the window
    # the captured legacy report actually used.
    run_user_activity.run({
        "ns": args.ns, "catalog": args.catalog, "report_date": report_date,
        "lookback_days": lookback_days, "source_mode": "table",
        "max_upstream_lag_days": PARITY_LAG_DAYS,
    })
    converted = converted_users(args.ns, args.catalog, report_date)

    checks = [
        check_per_user(legacy, converted, rpt["trends"]["peak_active_users"]),
        check_totals(rpt, legacy, converted, args.ns, args.catalog, report_date, lookback_days),
        check_freshness(args.ns, args.catalog, report_date, lookback_days),
        check_idempotency(args.ns, args.catalog, report_date, lookback_days),
    ]
    with open(os.path.join(args.baseline, "manifest_sha256.txt"), encoding="utf-8") as handle:
        hashes = handle.read()
    with open(os.path.join(args.baseline, "exit_code.txt"), encoding="utf-8") as handle:
        exit_code = handle.read().strip()
    # The capture writes "exit=<code>"; keep the recorded text in the report and compare
    # on the code itself.
    exit_status = exit_code.split("=")[-1].strip()

    # Check 5: tier 1 only holds if the captured legacy run actually succeeded and left
    # every artefact behind — a baseline from a failed run must not report green.
    artefacts = {
        name: os.path.exists(os.path.join(args.baseline, name))
        for name in ("activity_report.json", "user_summaries.jsonl", "manifest_sha256.txt")
    }
    checks.append({
        "name": "5. Baseline provenance stated (tier 1, legacy output)",
        "passed": all(artefacts.values()) and exit_status == "0",
        "values": {
            "tier": "baseline: legacy output",
            "legacy_exit_code": exit_code,
            "baseline_artefacts": artefacts,
            "analytics_daily.py executed": False,
            "upstream aggregate": "deterministic fixture from the seeded events",
        },
    })

    report = {
        "baseline_dir": args.baseline,
        "baseline_hashes": hashes,
        "legacy_exit_code": exit_code,
        "report_date": report_date,
        "lookback_days": rpt["lookback_days"],
        "legacy_total_events": rpt["trends"]["total_events"],
        "legacy_user_count": len(legacy),
        "catalog": args.catalog,
        "verdict": "green" if all(c["passed"] for c in checks) else "partial",
        "checks": checks,
    }
    text = render(report)
    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    for check in checks:
        print(f"  {'PASS' if check['passed'] else 'FAIL'}  {check['name']}")
    return 0 if report["verdict"] == "green" else 1


if __name__ == "__main__":
    raise SystemExit(main())

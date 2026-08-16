#!/usr/bin/env python3
"""Reconcile the estate rollup: the five acceptance checks of the gold contract.

Every check reads the live tables, the committed configuration, or a recorded artifact of a
real job run. Nothing here restates a number the rollup itself asserted: check 1 recomputes
each unit's verdict from that unit's own evidence tables and compares it to what is stored,
check 2 cross-foots CUSTBILL against the CSV the legacy Perl report actually produced on this
machine, check 3 reads back a real failed orchestrator run and then asks the live table
whether a green row exists for that run's date, check 4 compares detections against the seed
manifest's planted counts, and check 5 parses the committed Terraform.

Usage:
  export DATABRICKS_HOST="${DATABRICKS_DEMO_HOST%/}" DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
  python3 scripts/tp_databricks/recon_estate_rollup.py --ns demo --run-date 2026-08-16 \\
      --legacy-report /home/ubuntu/tp-golden/custbill/reports/finance_billing_20260816.csv \\
      --fail-drill /home/ubuntu/tp-golden/estate/faildrill-2026-08-18.json

Exit status is 0 only when all five checks are green.
"""

from __future__ import annotations

import argparse
import csv
import decimal
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = REPO_ROOT / "databricks" / "notebooks" / "estate_rollup.py"
TERRAFORM = REPO_ROOT / "infrastructure" / "terraform-databricks" / "jobs_estate_rollup.tf"
CLUSTER_KEYS = ("new_cluster", "existing_cluster_id", "job_cluster", "job_clusters")
# The DAG the contract requires, as (task, its upstream tasks). Compared against the
# committed Terraform, so an edge silently dropped there fails this check rather than
# surviving as prose in a report.
REQUIRED_EDGES = {
    "sftp_ingest": set(),
    "parse_custbill": {"sftp_ingest"},
    "finance_report": {"parse_custbill"},
    "analytics_daily": set(),
    "user_activity": {"analytics_daily"},
    "audit_archive": set(),
    "search_reindex": set(),
    "storage_cleanup": set(),
}


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dbx = _load(Path(__file__).with_name("dbx.py"), "tp_dbx")
pipeline = _load(NOTEBOOK, "tp_estate_rollup_notebook")


class Check:
    """One acceptance check and the numbers behind its verdict."""

    def __init__(self, key: str, title: str):
        self.key = key
        self.title = title
        self.findings: list[str] = []
        self.failures: list[str] = []

    def record(self, finding: str) -> None:
        self.findings.append(finding)

    def require(self, condition: bool, failure: str) -> bool:
        if not condition:
            self.failures.append(failure)
        return condition

    @property
    def result(self) -> str:
        return "green" if not self.failures else "red"

    def as_dict(self) -> dict:
        return {"check": self.key, "title": self.title, "result": self.result,
                "findings": self.findings, "failures": self.failures}


def _rows(statement: str) -> list[list]:
    return dbx.sql(statement)


def _q(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def check_one_row_per_unit(ns: str, run_date: str, catalog: str) -> Check:
    check = Check("1", "one row per converted unit, with a derived recon_result")
    specs = pipeline.unit_specs(catalog, ns)
    expected = {spec["unit"] for spec in specs}

    stored = {
        row[0]: {"result": row[1], "detail": row[2], "rows_in": int(row[3]), "rows_out": int(row[4]),
                 "rejected": int(row[5]), "job_run_id": row[6], "copies": int(row[7])}
        for row in _rows(
            "SELECT unit, max(recon_result), max(recon_detail), max(rows_in), max(rows_out), "
            f"max(rejected), max(job_run_id), count(*) FROM {catalog}.gold.estate_daily_rollup "
            f"WHERE ns = {_q(ns)} AND run_date = DATE{_q(run_date)} GROUP BY unit ORDER BY unit"
        )
    }
    check.record(f"units present: {len(stored)} of {len(expected)} ({', '.join(sorted(stored))})")
    check.require(set(stored) == expected, f"unit set mismatch: missing {sorted(expected - set(stored))}, "
                                           f"unexpected {sorted(set(stored) - expected)}")
    duplicated = {unit: row["copies"] for unit, row in stored.items() if row["copies"] != 1}
    check.require(not duplicated, f"units with more than one row for this slice: {duplicated}")

    # Recompute each verdict now, from that unit's own evidence tables, and compare it to the
    # stored value: a hand-entered or stale verdict cannot survive this, and the numbers in
    # recon_detail are re-derived rather than trusted.
    for spec in specs:
        measures = ", ".join(f"{expression} AS {alias}" for alias, expression in spec["measures"].items())
        recomputed = _rows(f"SELECT {spec['result']} AS result, rows_in, rows_out FROM (SELECT {measures})")[0]
        row = stored.get(spec["unit"])
        if row is None:
            continue
        # A measure over an absent slice is SQL NULL -- `storage_cleanup` relies on that to report
        # `blocked` -- while the stored side coalesces to 0. Compare on the stored side's terms, so
        # the unit with no evidence gets a red verdict instead of a traceback from int(None).
        agree = (recomputed[0] == row["result"] and int(recomputed[1] or 0) == row["rows_in"]
                 and int(recomputed[2] or 0) == row["rows_out"])
        check.require(
            agree,
            f"{spec['unit']}: stored ({row['result']}, rows_in={row['rows_in']}, rows_out={row['rows_out']}) "
            f"disagrees with a recomputation from its evidence tables "
            f"({recomputed[0]}, rows_in={recomputed[1]}, rows_out={recomputed[2]})",
        )
        check.record(
            f"{spec['unit']}: {row['result']} (recomputed {recomputed[0]}); "
            f"rows_in={row['rows_in']} rows_out={row['rows_out']} rejected={row['rejected']}; "
            f"job_run_id={row['job_run_id'] or '(local warehouse run)'}"
        )
    not_green = {unit: row["result"] for unit, row in stored.items() if row["result"] != "green"}
    check.require(not not_green, f"units not green: {not_green}")
    return check


def check_custbill_crossfoot(ns: str, catalog: str, legacy_report: Path,
                            report_date: str | None = None) -> Check:
    check = Check("2", "CUSTBILL cross-foots to the legacy report to the cent")
    if not legacy_report.exists():
        check.require(False, f"legacy baseline report not found at {legacy_report}; "
                             "regenerate it with `make legacy-etl-run JOB=finance_excel_report`")
        return check

    baseline: dict[tuple[str, str], tuple[int, decimal.Decimal]] = {}
    with legacy_report.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            baseline[(row["Currency"], row["RecordType"])] = (
                int(row["RecordCount"]), decimal.Decimal(row["TotalAmount"])
            )
    check.record(f"legacy baseline: {legacy_report} ({len(baseline)} currency/type groups)")

    # gold.finance_billing_summary is keyed by (ns, report_date, currency, record_type) and the
    # finance job replaces only its own date slice, so several business days can coexist for one
    # namespace. Comparing a single day's legacy report against the whole namespace would let
    # another day's rows stand in silently, so the date is resolved explicitly and named in the
    # evidence: either the caller states it, or there must be exactly one slice to compare.
    available = [str(row[0]) for row in _rows(
        f"SELECT DISTINCT report_date FROM {catalog}.gold.finance_billing_summary "
        f"WHERE ns = {_q(ns)} ORDER BY report_date")]
    if report_date is None:
        if not check.require(len(available) == 1,
                             f"gold.finance_billing_summary holds {len(available)} report_date slices for "
                             f"ns={ns} ({available}); pass --report-date so the comparison names one "
                             "business day rather than picking one"):
            return check
        report_date = available[0]
    pipeline.validate_run_date(report_date)
    check.require(report_date in available,
                  f"no gold.finance_billing_summary slice for report_date {report_date}; available {available}")
    check.record(f"migrated slice compared: report_date {report_date} (slices present: {available})")

    migrated = {
        (row[0], row[1]): (int(row[2]), decimal.Decimal(str(row[3])))
        for row in _rows(
            f"SELECT currency, record_type, record_count, total_amount FROM {catalog}.gold.finance_billing_summary "
            f"WHERE ns = {_q(ns)} AND report_date = DATE{_q(report_date)} ORDER BY currency, record_type"
        )
    }
    check.require(set(migrated) == set(baseline),
                  f"group mismatch: legacy {sorted(baseline)} vs migrated {sorted(migrated)}")
    for key in sorted(set(baseline) & set(migrated)):
        legacy_count, legacy_amount = baseline[key]
        gold_count, gold_amount = migrated[key]
        check.require(legacy_count == gold_count and legacy_amount == gold_amount,
                      f"{key[0]} {key[1]}: legacy {legacy_count}/{legacy_amount} != gold {gold_count}/{gold_amount}")
        check.record(f"{key[0]} {key[1]}: {gold_count} rows / {gold_amount} (legacy {legacy_count} / {legacy_amount})")

    legacy_total_rows = sum(count for count, _ in baseline.values())
    legacy_total_amount = sum((amount for _, amount in baseline.values()), decimal.Decimal("0"))
    parsed = int(_rows(f"SELECT count(*) FROM {catalog}.silver.custbill_records WHERE ns = {_q(ns)}")[0][0])
    parsed_amount = decimal.Decimal(str(_rows(
        f"SELECT coalesce(sum(amount), 0) FROM {catalog}.silver.custbill_records WHERE ns = {_q(ns)}")[0][0]))
    check.require(parsed == legacy_total_rows and parsed_amount == legacy_total_amount,
                  f"silver totals {parsed}/{parsed_amount} != legacy totals {legacy_total_rows}/{legacy_total_amount}")
    check.record(f"totals: {parsed} records / {parsed_amount} in silver (the whole ns={ns} parse slice; "
                 f"silver.custbill_records carries no report_date) == {legacy_total_rows} / "
                 f"{legacy_total_amount} in the legacy report")
    return check


def check_failure_path(ns: str, catalog: str, artifact: Path) -> Check:
    check = Check("3", "a real orchestrator run with a failing upstream writes no green rollup")
    if not artifact.exists():
        check.require(False, f"no failure-drill artifact at {artifact}; produce one with "
                             "`run_estate_dev.py run fail_unit=<unit> run_date=<date>`")
        return check
    drill = json.loads(artifact.read_text(encoding="utf-8"))
    tasks = {task["task_key"]: task for task in drill.get("tasks", [])}
    failed = [key for key, task in tasks.items() if task.get("result_state") == "FAILED"]
    skipped = [key for key, task in tasks.items() if task.get("result_state") == "UPSTREAM_FAILED"]

    check.record(f"run {drill.get('run_id')} of {drill.get('job')}: {drill.get('result_state')} "
                 f"({drill.get('url')})")
    check.record(f"deliberately failed upstream: {drill.get('fail_unit')}; failed tasks {sorted(failed)}; "
                 f"skipped on upstream failure {sorted(skipped)}")
    check.require(drill.get("result_state") == "FAILED",
                  f"the drill run did not fail: result_state={drill.get('result_state')}")
    check.require(drill.get("fail_unit") in failed,
                  f"the deliberately failed unit {drill.get('fail_unit')} is not among the failed tasks {failed}")
    check.require("estate_rollup" in skipped,
                  "the estate_rollup task was not skipped by the upstream failure")
    check.require(drill.get("job_torn_down") is True,
                  "the throwaway drill job was not deleted; the test artifact must be reverted")

    # Asked of the live table now, not read from the artifact: an upstream failure must leave
    # no rollup row at all for the drill's run_date, green or otherwise.
    drill_date = drill.get("run_date", "")
    pipeline.validate_run_date(drill_date)
    live = _rows(
        f"SELECT recon_result, count(*) FROM {catalog}.gold.estate_daily_rollup "
        f"WHERE ns = {_q(ns)} AND run_date = DATE{_q(drill_date)} GROUP BY recon_result"
    )
    check.record(f"live rollup rows for the drill's run_date {drill_date}: "
                 f"{ {row[0]: int(row[1]) for row in live} or 'none'}")
    check.require(not live, f"the failed run left rollup rows for {drill_date}: {live}")
    return check


def check_anomalies(ns: str, catalog: str) -> Check:
    check = Check("4", "seeded anomalies are present and traceable to the manifest")
    planted = {row[0]: (int(row[1]), row[2], row[3]) for row in _rows(
        f"SELECT kind, planted_count, target, manifest_sha256 FROM {catalog}.bronze.seed_anomaly_manifest "
        f"WHERE ns = {_q(ns)} ORDER BY kind")}
    check.require(bool(planted), f"no seed manifest landed for ns={ns}; "
                                 "run `run_estate_rollup.py manifest` after `make seed-legacy`")
    if planted:
        digests = {value[2] for value in planted.values()}
        check.record(f"manifest sha256 {', '.join(sorted(digests))}; planted "
                     f"{ {kind: value[0] for kind, value in planted.items()} }")

    detected: dict[str, int] = {}
    coverage_gaps: dict[str, int] = {}
    for row in _rows(
        f"SELECT anomaly_type, unit, count(*) FROM {catalog}.gold.estate_anomalies "
        f"WHERE ns = {_q(ns)} GROUP BY anomaly_type, unit ORDER BY anomaly_type"
    ):
        anomaly_type, unit, count = row[0], row[1], int(row[2])
        if unit == "seed_manifest":
            coverage_gaps[anomaly_type] = count
        else:
            detected[anomaly_type] = detected.get(anomaly_type, 0) + count
        check.record(f"{anomaly_type} via {unit}: {count} row(s)")

    untraceable = int(_rows(
        f"SELECT count(*) FROM {catalog}.gold.estate_anomalies a WHERE a.ns = {_q(ns)} AND NOT EXISTS "
        f"(SELECT 1 FROM {catalog}.bronze.seed_anomaly_manifest m WHERE m.ns = a.ns AND m.kind = a.anomaly_type)"
    )[0][0])
    check.require(untraceable == 0, f"{untraceable} anomaly rows cite no manifest entry")
    check.record(f"anomaly rows with no manifest entry: {untraceable}")

    for kind, (count, target, _digest) in sorted(planted.items()):
        if kind in detected:
            check.require(detected[kind] == count,
                          f"{kind}: detected {detected[kind]} but the manifest planted {count} in {target}")
        else:
            check.require(kind in coverage_gaps,
                          f"{kind}: planted {count} in {target} but neither detected nor recorded as a "
                          "coverage gap, so a real anomaly would be invisible")
    return check


def check_job_configuration() -> Check:
    check = Check("5", "committed job configuration: serialized, no sleep, no cluster")
    if not TERRAFORM.exists():
        check.require(False, f"{TERRAFORM} is missing")
        return check
    text = TERRAFORM.read_text(encoding="utf-8")

    jobs = re.findall(r'resource "databricks_job" "(\w+)"', text)
    check.record(f"jobs defined: {', '.join(jobs)}")
    # The digit boundary matters: without it, max_concurrent_runs = 10 would satisfy the check
    # that the estate cannot run concurrently, which is the legacy defect being retired.
    check.require(len(re.findall(r"max_concurrent_runs\s*=\s*1(?!\d)", text)) == len(jobs),
                  "not every job in the file sets max_concurrent_runs = 1")

    sleeps = [line for line in text.splitlines() if re.search(r"\bsleep\b", line)]
    check.require(not sleeps, f"sleep-based ordering found: {sleeps}")
    check.record("sleep-based ordering: none")

    clusters = [key for key in CLUSTER_KEYS if re.search(rf"\b{key}\b", text)]
    check.require(not clusters, f"cluster configuration found: {clusters}")
    check.record(f"cluster configuration: none ({', '.join(CLUSTER_KEYS)} all absent)")

    # Parse the orchestrator's task blocks so a missing DAG edge fails here.
    orchestrator = text.split('resource "databricks_job" "estate_orchestrator"', 1)[-1]
    edges: dict[str, set[str]] = {}
    for block in re.split(r"\n\s*(?:dynamic \"task\"|task) \{", orchestrator)[1:]:
        key_match = re.search(r'task_key\s*=\s*"([\w]+)"', block)
        if not key_match:
            continue
        body = block.split("task_key", 1)[1]
        edges[key_match.group(1)] = set(re.findall(r'depends_on \{\s*task_key\s*=\s*"(\w+)"', body))
    rollup_upstream = edges.pop("estate_rollup", set())
    check.record(f"orchestrator edges: { {task: sorted(up) for task, up in sorted(edges.items())} }")
    check.record(f"estate_rollup depends on: {sorted(rollup_upstream) or 'a dynamic leaf list'}")
    for task, upstream in REQUIRED_EDGES.items():
        check.require(task in edges, f"orchestrator has no {task} task")
        if task in edges:
            check.require(edges[task] == upstream,
                          f"{task} upstream edges {sorted(edges[task])} != required {sorted(upstream)}")
    check.require("run_job_task" in text, "the orchestrator does not order units through run_job_task edges")
    check.require("estate_rollup_leaf_tasks" in text or rollup_upstream,
                  "the estate_rollup task has no upstream edges, so it could run against a half-finished estate")
    return check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--catalog", default=dbx.CATALOG)
    parser.add_argument("--run-date", required=True, help="run_date of the rollup slice under recon")
    parser.add_argument("--legacy-report", type=Path,
                        default=Path("/home/ubuntu/tp-golden/custbill/reports/finance_billing_20260816.csv"),
                        help="CSV the legacy Perl finance report produced, the CUSTBILL baseline")
    parser.add_argument("--report-date", default=None,
                        help="report_date of the gold finance slice to cross-foot; required only when "
                             "more than one business day is present for the namespace")
    parser.add_argument("--fail-drill", type=Path,
                        default=Path("/home/ubuntu/tp-golden/estate/faildrill-2026-08-18.json"),
                        help="recorded run summary of the orchestrator failure drill")
    parser.add_argument("--json", action="store_true", help="emit the findings as JSON")
    args = parser.parse_args(argv)

    pipeline.validate_ns(args.ns)
    pipeline.validate_run_date(args.run_date)
    pipeline.validate_identifier(args.catalog, "catalog")

    checks = [
        check_one_row_per_unit(args.ns, args.run_date, args.catalog),
        check_custbill_crossfoot(args.ns, args.catalog, args.legacy_report, args.report_date),
        check_failure_path(args.ns, args.catalog, args.fail_drill),
        check_anomalies(args.ns, args.catalog),
        check_job_configuration(),
    ]

    if args.json:
        print(json.dumps({"ns": args.ns, "run_date": args.run_date,
                          "checks": [check.as_dict() for check in checks]}, indent=2))
    else:
        for check in checks:
            print(f"\n[{check.result.upper()}] check {check.key}: {check.title}")
            for finding in check.findings:
                print(f"  - {finding}")
            for failure in check.failures:
                print(f"  ! {failure}")
        green = sum(1 for check in checks if check.result == "green")
        print(f"\nestate recon for ns={args.ns} run_date={args.run_date}: {green}/{len(checks)} checks green")
    return 0 if all(check.result == "green" for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

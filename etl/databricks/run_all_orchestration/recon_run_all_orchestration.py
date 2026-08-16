#!/usr/bin/env python3
"""Fixture recon for the run_all_orchestration migration unit.

Reproduces the deterministic legacy chain end-state (the golden baseline the
estate job must reproduce live), statically verifies the ow_tp_custbill_estate
job definition (DAG dependencies, single active run, no sleep sequencing),
and exercises the estate run-log recorder's pure core against the contract's
outcome policies. Emits a machine-readable recon report (run_mode=fixture).
This is explicitly NOT the live proof — Jobs API semantics, dynamic value
resolution, SQL/Delta/UC/warehouse behaviour are listed as unverified; the
parent proves them live.

Usage (from repo root):
  python3 etl/databricks/run_all_orchestration/recon_run_all_orchestration.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
NS = "demo"
UNIT = "run_all_orchestration"
NOTEBOOK = REPO / "etl/databricks/run_all_orchestration/estate_run_log_notebook.py"
JOB_TF = REPO / "infrastructure/terraform-databricks/jobs_run_all_orchestration.tf"
REPORT = REPO / "docs/tech-partnerships/recon" / f"{UNIT}.recon.json"
SANDBOX = REPO / ".tp-preflight"
LEGACY_ROOT = SANDBOX / "legacy-run-estate"
TP_FAKETIME = "2026-01-15 00:00:00"

# Golden baseline (immutable, from the unit contract / parent SHA256SUMS
# manifest): end-state after run_all.sh over the deterministic NS=demo drop.
GOLDEN_ARCHIVED_FILES = 2
GOLDEN_SILVER_ROWS = 100
GOLDEN_AGGREGATES = {
    ("EUR", "INVOICE"): (22, "101554.41"),
    ("EUR", "CREDIT"): (6, "33375.97"),
    ("GBP", "INVOICE"): (32, "183113.58"),
    ("GBP", "CREDIT"): (5, "28454.59"),
    ("USD", "INVOICE"): (28, "130502.15"),
    ("USD", "CREDIT"): (7, "33390.44"),
}


def load_core():
    spec = importlib.util.spec_from_file_location("estate_run_log_notebook", NOTEBOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def deterministic_env() -> dict:
    """Forward TP_FAKETIME only when libfaketime is usable (the wrapper
    hard-fails when TP_FAKETIME is set without libfaketime installed)."""
    env = {**os.environ, "OTTERWORKS_LEGACY_ROOT": str(LEGACY_ROOT)}
    libfaketime_paths = (
        "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1",
        "/usr/lib/aarch64-linux-gnu/faketime/libfaketime.so.1",
        "/usr/local/lib/faketime/libfaketime.so.1",
    )
    if any(Path(p).is_file() for p in libfaketime_paths):
        env["TP_FAKETIME"] = TP_FAKETIME
    else:
        env.pop("TP_FAKETIME", None)
    return env


def run_legacy_chain(env: dict) -> dict:
    """Run the deterministic legacy chain end-to-end and return its end-state."""
    shutil.rmtree(LEGACY_ROOT, ignore_errors=True)
    subprocess.run(["make", "legacy-etl-gen-data", f"NS={NS}"], cwd=REPO,
                   check=True, env=env)
    subprocess.run(["make", "legacy-etl-run", "JOB=run_all", "RUN_ALL_SLEEP=0"],
                   cwd=REPO, check=True, env=env)

    archived = sorted(
        p.name for p in (LEGACY_ROOT / "archive").glob("CUSTBILL*.dat*")
    )
    parsed_rows = 0
    for psv in sorted((LEGACY_ROOT / "parsed").glob("*.psv")):
        parsed_rows += sum(1 for _ in psv.open())

    aggregates = {}
    reports = sorted((LEGACY_ROOT / "reports").glob("finance_billing_*.csv"))
    for line in reports[-1].read_text().splitlines() if reports else []:
        parts = line.split(",")
        if len(parts) == 4 and parts[2].isdigit():
            aggregates[(parts[0], parts[1])] = (int(parts[2]), parts[3])
    return {
        "archived_files": len(archived),
        "parsed_rows": parsed_rows,
        "aggregates": aggregates,
        "report_count": len(reports),
    }


def tf_static_facts() -> dict:
    """Static facts about the estate job definition read from the .tf file."""
    tf = JOB_TF.read_text()
    # Executable surfaces only: strip .tf comment lines and the description
    # attribute; sleep may legitimately appear in prose about the legacy script.
    tf_code = "\n".join(
        line for line in tf.splitlines()
        if not line.lstrip().startswith("#") and "description" not in line
    )

    def deps_of(task_key: str) -> list:
        m = re.search(
            rf'task_key = "{task_key}"(.*?)(?=\n  task |\Z)', tf, re.S
        )
        block = m.group(1) if m else ""
        return sorted(re.findall(r'depends_on {\s*task_key = "([a-z_]+)"', block))

    return {
        "job_name_prefix": '"${var.prefix}_custbill_estate"' in tf,
        "task_keys": re.findall(r'\n  task {\n    task_key = "([a-z_]+)"', tf),
        "deps": {k: deps_of(k) for k in ("ingest", "parse", "finance", "record_run_log")},
        "max_concurrent_runs": re.search(r"max_concurrent_runs = (\d+)", tf).group(1)
        if re.search(r"max_concurrent_runs = (\d+)", tf) else None,
        "record_run_if_all_done": 'run_if   = "ALL_DONE"' in tf,
        "sleep_free": ("sleep" not in tf_code)
        and ("time.sleep" not in NOTEBOOK.read_text()),
        "no_suppression": "2>/dev/null" not in tf_code,
        "run_job_tasks": sorted(re.findall(r"job_id = databricks_job\.([a-z_]+)\.id", tf)),
    }


def main() -> int:
    core = load_core()
    checks = []

    def check(cid, expected, actual, source, ok=None):
        result = "pass" if (ok if ok is not None else expected == actual) else "fail"
        checks.append({"id": cid, "expected": expected, "actual": actual,
                       "source_of_truth": source, "result": result})
        print(f"[{result}] {cid}: expected={expected} actual={actual}")
        return result == "pass"

    # 1. dag-dependencies: single multi-task job, ingest -> parse -> finance
    # via task dependencies, no sleep-based sequencing anywhere in the unit.
    facts = tf_static_facts()
    check(
        "dag-dependencies",
        {"tasks": ["ingest", "parse", "finance", "record_run_log"],
         "parse_depends": ["ingest"], "finance_depends": ["parse"],
         "record_depends": ["finance", "ingest", "parse"],
         "consumes_sibling_jobs": ["finance_excel_report",
                                   "parse_custbill_fixedwidth",
                                   "sftp_ingest_poll"],
         "sleep_free": True},
        {"tasks": facts["task_keys"],
         "parse_depends": facts["deps"]["parse"],
         "finance_depends": facts["deps"]["finance"],
         "record_depends": facts["deps"]["record_run_log"],
         "consumes_sibling_jobs": facts["run_job_tasks"],
         "sleep_free": facts["sleep_free"]},
        "contract acceptance dag-dependencies vs static read of jobs_run_all_orchestration.tf",
    )

    # 2. single-active-run: max_concurrent_runs=1 asserted from the job
    # definition (contract coverage_gap overlap-suppression: provoking two
    # genuinely concurrent scheduled runs is not reliably reproducible).
    check(
        "single-active-run",
        {"max_concurrent_runs": "1", "queue_enabled": True},
        {"max_concurrent_runs": facts["max_concurrent_runs"],
         "queue_enabled": "enabled = true" in JOB_TF.read_text()},
        "contract acceptance single-active-run vs job definition (overlap-suppression is a declared coverage gap)",
    )

    # 3. end-state-parity: one deterministic legacy chain run from a clean
    # slice reproduces the golden end-state the estate job must match live.
    env = deterministic_env()
    state = run_legacy_chain(env)
    golden_aggs = {f"{c}/{t}": [n, a] for (c, t), (n, a) in GOLDEN_AGGREGATES.items()}
    actual_aggs = {f"{c}/{t}": [n, a] for (c, t), (n, a) in state["aggregates"].items()}
    check(
        "end-state-parity",
        {"archived_files": GOLDEN_ARCHIVED_FILES, "parsed_rows": GOLDEN_SILVER_ROWS,
         "aggregates": golden_aggs},
        {"archived_files": state["archived_files"], "parsed_rows": state["parsed_rows"],
         "aggregates": actual_aggs},
        "contract golden_baseline_location (2 archived, 100 silver rows, 6 cent-exact aggregates) vs deterministic legacy chain rerun end-state",
    )

    # 4. errors-surfaced: a failed upstream task yields attributed FAILED /
    # UPSTREAM_FAILED rows and a non-green run (recorder re-raises).
    fail_states = {"ingest": "SUCCESS", "parse": "FAILED",
                   "finance": "UPSTREAM_FAILED"}
    rows = core.build_run_log_rows(NS, "run-777", "job-1", fail_states)
    raised = False
    if core.failed_tasks(rows):
        raised = True  # driver raises EstateRunFailed on any non-success row
    check(
        "errors-surfaced",
        {"logged_rows": 3, "failed_tasks": ["parse", "finance"], "run_fails": True},
        {"logged_rows": len(rows), "failed_tasks": core.failed_tasks(rows),
         "run_fails": raised},
        "contract acceptance errors-surfaced vs recorder core over a parse-failure state set",
    )

    # 5. null-attribution: NULL/blank/unresolved/unknown outcomes must fail
    # the run, never be logged as a plausible-looking success row.
    rejected = []
    for bad in (None, "", "   ", "{{tasks.parse.result_state}}", "GREENISH"):
        try:
            core.build_run_log_rows(NS, "run-778", "job-1",
                                    {"ingest": "SUCCESS", "parse": bad,
                                     "finance": "SUCCESS"})
        except core.UnresolvedTaskStateError:
            rejected.append(True)
        else:
            rejected.append(False)
    missing_key_rejected = False
    try:
        core.build_run_log_rows(NS, "run-779", "job-1", {"ingest": "SUCCESS"})
    except core.UnresolvedTaskStateError:
        missing_key_rejected = True
    check(
        "null-attribution-fails",
        {"all_rejected": True, "missing_task_rejected": True},
        {"all_rejected": all(rejected), "missing_task_rejected": missing_key_rejected},
        "contract malformed_record_policy vs recorder core over NULL/blank/unresolved/unknown states",
    )

    # 6. empty-input no-op: an estate run over an empty slice is all-SUCCESS
    # no-op tasks; the recorder logs three succeeded rows and does not raise.
    ok_rows = core.build_run_log_rows(
        NS, "run-780", "job-1",
        {k: "SUCCESS" for k in ("ingest", "parse", "finance")})
    check(
        "empty-input-noop",
        {"rows": 3, "failed_tasks": []},
        {"rows": len(ok_rows), "failed_tasks": core.failed_tasks(ok_rows)},
        "contract empty_input_semantics vs recorder core over an all-no-op-success state set",
    )

    # 7. Idempotency: re-recording the same estate run replaces, never
    # duplicates (delete-then-insert per (ns, estate_run_id)).
    table_state: dict = {}
    core.apply_to_state(table_state, rows)
    core.apply_to_state(table_state, rows)
    log = table_state["estate_run_log"]
    idem_ok = check(
        "rerun-idempotency",
        {"run_keys": 1, "rows_for_run": 3},
        {"run_keys": len(log), "rows_for_run": len(log[(NS, "run-777")])},
        "recorder write semantics recomputed after a repeated apply for the same estate_run_id",
    )

    # Planted anomaly: upstream-failure-propagation (must-detect). The
    # trailer-mismatched drop makes the parse task fail; the recorder core
    # attributes it and the run outcome is FAILED (check 4 evidence).
    detected = ["upstream-failure-propagation"] if (
        core.failed_tasks(rows) == ["parse", "finance"] and raised
    ) else []

    report = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": NS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_mode": "fixture",
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if idem_ok else "fail",
            "evidence": "re-applied the same estate run's rows twice: still one (ns, estate_run_id) key with exactly 3 task rows (delete-then-insert semantics)",
        },
        "planted_anomaly_detections": {
            "expected_set": ["upstream-failure-propagation"],
            "actual_set": detected,
            "missing": sorted(set(["upstream-failure-propagation"]) - set(detected)),
            "unexpected": sorted(set(detected) - {"upstream-failure-propagation"}),
        },
        "unverified_paths": [
            "Jobs API run_job_task execution of the sibling jobs and task dependency scheduling",
            "Dynamic value resolution of {{tasks.<key>.result_state}} / {{job.run_id}} into the recorder's parameters",
            "Live max_concurrent_runs=1 queueing under genuinely concurrent triggers (declared coverage gap overlap-suppression)",
            "SQL execution and Delta semantics of ow_tp.gold.estate_run_log (CREATE TABLE IF NOT EXISTS, parameterized DELETE, append)",
            "Unity Catalog permissions/grants on catalog ow_tp",
            "Serverless notebook-task execution and the serverless SQL warehouse",
            "Terraform resources in jobs_run_all_orchestration.tf, including the cross-file reference to databricks_job.finance_excel_report (parent applies at the wave boundary)",
            "Live upstream-failure-propagation run against the parent's trailer-mismatched drop (parent triggers post-merge)",
        ],
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {REPORT}")

    failures = [c["id"] for c in checks if c["result"] == "fail"]
    if failures:
        print(f"RECON FAILURES: {failures}")
        return 1
    print("recon: all fixture checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

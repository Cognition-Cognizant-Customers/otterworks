#!/usr/bin/env python3
"""Deploy, run and reconcile the CUSTBILL chain job on Databricks.

This is the conversion of the legacy orchestration (etl/legacy-extra/crontab +
run_all.sh) into one dependency-driven Databricks Workflow. Every command is
namespace-scoped: tables are suffixed `_<ns>`, volume paths live under `<ns>/`,
and nothing here creates a catalog, schema, volume or cluster.

  deploy        create the unit tables, import the task SQL, upsert the job
  land          upload local CUSTBILL drops to the landing volume
  clear-drop    remove the namespace drop files from the landing volume
  run           trigger the chain (Run Now) and wait for it
  fail-test     land a trailer-corrupt drop, prove failure propagation, clean up
  concurrency   trigger two runs and record that the second queues
  recon         recompute everything from Databricks and diff against the
                golden legacy output, then write the recon report

Usage:
  python3 scripts/tp_dbx/chain.py deploy --ns cnvorch
  python3 scripts/tp_dbx/chain.py land   --ns cnvorch --drop-dir <dir>
  python3 scripts/tp_dbx/chain.py run    --ns cnvorch
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import chain_sql as S
from chain_sql import ChainNames
from client import Databricks, DbxError, require_ns

EVIDENCE_DIR = pathlib.Path(os.environ.get("TP_CHAIN_EVIDENCE_DIR", "/tmp"))
# RFC 2606 reserved domain: a routable address would page a real distribution list,
# and Databricks silently drops an .invalid recipient from email_notifications.
FAILURE_ALERT_ADDRESS = "ow-tp-alerts@otterworks.example.com"
# result states that mean a task actually executed, as opposed to being cut off
# by an upstream failure (UPSTREAM_FAILED), skipped, or excluded by run_if.
RAN_STATES = {
    "SUCCESS",
    "FAILED",
    "TIMEDOUT",
    "CANCELED",
}
BAD_TRAILER_FILE = "CUSTBILL_BADTRL_999.dat"
MARKER_PAYLOAD = (
    b"chain drop marker: keeps read_files metadata resolvable on an empty batch\n"
)
# Paths this unit does NOT prove. Stated here rather than discovered at rollup.
UNVERIFIED_PATHS = (
    (
        "email delivery of the on_failure notification: the recipient is a reserved "
        "example.com address, so only its presence in live job settings is verified, "
        "never that a message arrived"
    ),
    (
        "non-numeric amount handling in a CUSTBILL body record: contract coverage_gap "
        "owned by the parse unit, not exercised here"
    ),
    (
        "non-UTF-8 bytes in a drop file: the chain stores raw_line untouched and never "
        "normalises, but no invalid-byte drop was landed to prove byte transparency"
    ),
    (
        "the PAUSED schedule firing: verified only as pause_status=PAUSED in job "
        "settings, never by letting the 02:10 trigger run"
    ),
    (
        "infrastructure/terraform-databricks/jobs_orchestrate.tf: contributed as code "
        "and matched by hand against the live job settings; the unit may not run "
        "terraform plan/apply, so it is unapplied and un-planned here"
    ),
    (
        "a stage timing out rather than failing: timeout_seconds=1800 is configured "
        "but no run was driven past it"
    ),
)


# --- job definition ---------------------------------------------------------
def job_settings(dbx: Databricks, n: ChainNames) -> dict:
    """The whole point of the unit: declared dependencies instead of a crontab
    offset and `sleep 600`, one run at a time, bounded retries, and a failure
    path that records the failure."""
    params = {"ns": "{{job.parameters.ns}}", "run_id": "{{job.run_id}}"}

    def sql_task(
        task_key: str, depends_on: list[str], run_if: str | None = None
    ) -> dict:
        task = {
            "task_key": task_key,
            "sql_task": {
                "warehouse_id": dbx.warehouse_id,
                "file": {"path": n.task_sql_path(task_key), "source": "WORKSPACE"},
                "parameters": params,
            },
            "max_retries": 2,
            "min_retry_interval_millis": 15000,
            "retry_on_timeout": False,
            "timeout_seconds": 1800,
        }
        if depends_on:
            task["depends_on"] = [{"task_key": key} for key in depends_on]
        if run_if:
            task["run_if"] = run_if
        return task

    return {
        "name": n.job_name,
        "description": (
            "CUSTBILL chain: ingest -> parse -> finance with declared task "
            "dependencies, replacing etl/legacy-extra/crontab offsets and "
            "run_all.sh's sleep-based 'dependency management'."
        ),
        "tags": {
            "project": "otterworks-tp",
            "unit": "dbx-orchestrate",
            "namespace": n.ns,
        },
        "max_concurrent_runs": 1,
        "queue": {"enabled": True},
        "parameters": [{"name": "ns", "default": n.ns}],
        "tasks": [
            sql_task("validate_params", []),
            sql_task("ingest", ["validate_params"]),
            sql_task("parse", ["ingest"]),
            sql_task("finance", ["parse"]),
            sql_task("chain_complete", ["finance"], run_if="ALL_SUCCESS"),
            # the honest replacement for "run_all done (probably)": a failed
            # stage lands a 'failed' ledger row instead of a success message
            {
                **sql_task(
                    "chain_failed",
                    ["validate_params", "ingest", "parse", "finance"],
                    run_if="AT_LEAST_ONE_FAILED",
                ),
                "max_retries": 0,
            },
        ],
        "email_notifications": {
            "on_failure": [FAILURE_ALERT_ADDRESS],
            "no_alert_for_skipped_runs": False,
        },
        # the legacy 02:10 finance slot, but paused: demo runs are Run Now only
        "schedule": {
            "quartz_cron_expression": "0 10 2 * * ?",
            "timezone_id": "UTC",
            "pause_status": "PAUSED",
        },
    }


# --- commands ---------------------------------------------------------------
def cmd_deploy(dbx: Databricks, n: ChainNames, args) -> int:
    for statement in S.tables(n):
        dbx.sql_ok(statement)
    print(f"tables ready: {n.bronze}, {n.silver}, {n.gold}, {n.ledger}")
    dbx.ok("POST", "/api/2.0/workspace/mkdirs", {"path": n.workspace_dir})
    for task_key, builder in S.TASK_SQL.items():
        dbx.ok(
            "POST",
            "/api/2.0/workspace/import",
            {
                "path": n.task_sql_path(task_key),
                "format": "AUTO",
                "overwrite": True,
                "content": base64.b64encode(builder(n).encode()).decode(),
            },
        )
    print(f"task SQL imported under {n.workspace_dir}")
    dbx.put_file(n.drop_marker, MARKER_PAYLOAD)
    print(f"drop marker present: {n.drop_marker}")
    job_id = dbx.upsert_job(job_settings(dbx, n))
    print(f"job {n.job_name} -> {dbx.host}/jobs/{job_id} (schedule PAUSED)")
    return 0


def _golden_drops(args) -> list[pathlib.Path]:
    """The bytes the legacy chain actually ingested: its upload directory if the
    drops are still there, otherwise the archived copies the legacy ingest moved
    into incoming/ (same bytes, `.done` suffix appended by the ksh job)."""
    upload = sorted(
        p for p in pathlib.Path(args.drop_dir).glob("CUSTBILL*.dat") if p.is_file()
    )
    if upload:
        return upload
    incoming = pathlib.Path(args.legacy_root) / "incoming"
    return sorted(p for p in incoming.glob("CUSTBILL*.dat.done") if p.is_file())


def cmd_land(dbx: Databricks, n: ChainNames, args) -> int:
    files = _golden_drops(args)
    if not files:
        raise SystemExit(
            f"no CUSTBILL drops in {args.drop_dir} or {args.legacy_root}/incoming; "
            "run the deterministic legacy baseline first"
        )
    for path in files:
        payload = path.read_bytes()
        name = path.name.removesuffix(".done")
        dbx.put_file(f"{n.drop_dir}/{name}", payload)
        print(f"landed {name} ({len(payload)} bytes) -> {n.drop_dir}/{name}")
    return 0


def cmd_clear_drop(dbx: Databricks, n: ChainNames, args) -> int:
    for entry in dbx.list_dir(n.drop_dir):
        if entry.get("is_directory") or entry["path"] == n.drop_marker:
            continue
        status = dbx.delete_file(entry["path"])
        print(f"deleted {entry['path']} (HTTP {status})")
    return 0


def _trigger(dbx: Databricks, n: ChainNames, ns_param: str | None = None) -> int:
    job = dbx.find_job(n.job_name)
    if not job:
        raise SystemExit(f"job {n.job_name} not found; run deploy first")
    params = {"ns": n.ns if ns_param is None else ns_param}
    return dbx.run_job(int(job["job_id"]), params)


def _run_report(dbx: Databricks, run: dict) -> dict:
    state = run.get("state", {})
    tasks = {
        t["task_key"]: t.get("state", {}).get("result_state")
        for t in run.get("tasks", [])
    }
    return {
        "run_id": run.get("run_id"),
        "url": dbx.run_url(int(run.get("run_id", 0))),
        "result_state": state.get("result_state"),
        "life_cycle_state": state.get("life_cycle_state"),
        "tasks": tasks,
    }


def cmd_run(dbx: Databricks, n: ChainNames, args) -> int:
    run_id = _trigger(dbx, n, args.ns_param)
    print(f"triggered {dbx.run_url(run_id)}")
    report = _run_report(dbx, dbx.wait_run(run_id))
    print(json.dumps(report, indent=2))
    _record_evidence(n, args.evidence_key or "last_run", report)
    return 0 if report["result_state"] == "SUCCESS" else 1


def cmd_concurrency(dbx: Databricks, n: ChainNames, args) -> int:
    """max_concurrent_runs=1: the second trigger queues instead of racing the
    first, which is what the crontab's overlapping :00/:05 entries could not do."""
    first = _trigger(dbx, n)
    second = _trigger(dbx, n)
    observed = []
    for _ in range(30):
        detail = dbx.ok("GET", f"/api/2.1/jobs/runs/get?run_id={second}")
        life_cycle = detail.get("state", {}).get("life_cycle_state")
        observed.append(life_cycle)
        if life_cycle in {"QUEUED", "PENDING"} and "QUEUED" in observed:
            break
        if life_cycle in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
            break
        time.sleep(5)
    first_report = _run_report(dbx, dbx.wait_run(first))
    second_report = _run_report(dbx, dbx.wait_run(second))
    evidence = {
        "first": first_report,
        "second": second_report,
        "second_observed_life_cycle_states": observed,
        "second_queued": "QUEUED" in observed,
    }
    print(json.dumps(evidence, indent=2))
    _record_evidence(n, "concurrency", evidence)
    return 0


def cmd_fail_test(dbx: Databricks, n: ChainNames, args) -> int:
    """Plant a trailer-corrupt drop so the parse stage fails, and prove the
    downstream finance stage does not run and the run reports failure."""
    body = [f"HDR CUSTBILL EXTRACT NS={n.ns.upper()}    FILE=999".ljust(65)]
    body.append(
        "C000999999"
        + "BADTRAILER CORP".ljust(30)
        + "20260115"
        + "000000010000"
        + "USD"
        + "01"
    )
    body.append("TRL" + "0000000042".ljust(62))
    payload = ("\n".join(body) + "\n").encode()
    dbx.put_file(f"{n.drop_dir}/{BAD_TRAILER_FILE}", payload)
    print(f"planted {BAD_TRAILER_FILE} (trailer says 42, body has 1 record)")
    try:
        run_id = _trigger(dbx, n)
        report = _run_report(dbx, dbx.wait_run(run_id))
        print(json.dumps(report, indent=2))
        report_run_id = int(report["run_id"])
        ledger = dbx.sql_ok(
            f"SELECT task_key, status, detail FROM {n.ledger} "
            f"WHERE run_id = '{report_run_id}' ORDER BY recorded_at"
        ).dicts()
        _record_evidence(n, "failure_run", {**report, "ledger": ledger})
        return 0 if report["result_state"] == "FAILED" else 1
    finally:
        # Clean up the planted file and its bronze rows (unit-owned table only).
        dbx.delete_file(f"{n.drop_dir}/{BAD_TRAILER_FILE}")
        dbx.sql_ok(f"DELETE FROM {n.bronze} WHERE source_file = '{BAD_TRAILER_FILE}'")
        print("cleanup: planted drop deleted and its bronze rows removed")


def cmd_empty_input(dbx: Databricks, n: ChainNames, args) -> int:
    """Contract's empty-input policy: a run with an empty drop directory is a
    no-op that preserves prior output. Proven live, then the drop is restored."""
    before_rows = int(dbx.sql_ok(f"SELECT count(*) FROM {n.silver}").scalar())
    before_md5 = hashlib.md5(_gold_csv(dbx, n).encode()).hexdigest()
    try:
        cmd_clear_drop(dbx, n, args)
        report = _run_report(dbx, dbx.wait_run(_trigger(dbx, n)))
        after_rows = int(dbx.sql_ok(f"SELECT count(*) FROM {n.silver}").scalar())
        after_md5 = hashlib.md5(_gold_csv(dbx, n).encode()).hexdigest()
        evidence = {
            **report,
            "silver_rows_before": before_rows,
            "silver_rows_after": after_rows,
            "gold_md5_before": before_md5,
            "gold_md5_after": after_md5,
        }
        print(json.dumps(evidence, indent=2))
        _record_evidence(n, "empty_input", evidence)
        return 0 if report["result_state"] == "SUCCESS" else 1
    finally:
        cmd_land(dbx, n, args)


def cmd_missing_param(dbx: Databricks, n: ChainNames, args) -> int:
    """A missing/blank mandatory run parameter must fail the run rather than
    default to some namespace and quietly write the wrong slice."""
    report = _run_report(dbx, dbx.wait_run(_trigger(dbx, n, ns_param="")))
    print(json.dumps(report, indent=2))
    _record_evidence(n, "missing_param", report)
    return 0 if report["result_state"] == "FAILED" else 1


def _record_evidence(n: ChainNames, key: str, value) -> None:
    path = EVIDENCE_DIR / f"chain-{n.ns}-evidence.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data[key] = value
    path.write_text(json.dumps(data, indent=2))
    print(f"evidence[{key}] -> {path}")


# --- reconciliation ---------------------------------------------------------
def _legacy_golden(root: pathlib.Path) -> dict:
    """The golden baseline is whatever the unmodified legacy chain actually
    produced in this namespace's run root — never a number from a document."""
    incoming = sorted(root.glob("incoming/CUSTBILL*.dat*"))
    parsed = sorted(root.glob("parsed/CUSTBILL*.psv"))
    reports = sorted(root.glob("reports/finance_billing_*.csv"))
    if not (incoming and parsed and reports):
        raise SystemExit(
            f"golden legacy output missing under {root}; run:\n"
            f"  export OTTERWORKS_LEGACY_ROOT={root}\n"
            "  make legacy-etl-gen-data NS=<ns>\n"
            "  TP_FAKETIME='2026-01-15 00:00:00' scripts/tp-run-deterministic.sh "
            "make legacy-etl-run JOB=run_all NS=<ns>"
        )
    files = {}
    for path in incoming:
        name = path.name.removesuffix(".done")
        files[name] = path.stat().st_size
    rows = sum(
        1 for path in parsed for line in path.read_text().splitlines() if line.strip()
    )
    report_bytes = reports[-1].read_bytes()
    return {
        "landed_files": files,
        "silver_rows": rows,
        "report_path": str(reports[-1]),
        "report_csv": report_bytes.decode(),
        "report_md5": hashlib.md5(report_bytes).hexdigest(),
    }


def _gold_csv(dbx: Databricks, n: ChainNames) -> str:
    """Render the gold aggregate in the legacy report's exact CSV format so the
    comparison is byte-for-byte rather than eyeballed."""
    result = dbx.sql_ok(S.gold_export_csv_query(n))
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["Currency", "RecordType", "RecordCount", "TotalAmount"])
    for currency, record_type, count, total in result.rows:
        writer.writerow([currency, record_type, count, total])
    return buffer.getvalue()


def _check(checks: list, check_id: str, expected, actual, source: str) -> None:
    checks.append(
        {
            "id": check_id,
            "expected": expected,
            "actual": actual,
            "source_of_truth": source,
            "result": "pass" if expected == actual else "fail",
        }
    )


def cmd_recon(dbx: Databricks, n: ChainNames, args) -> int:
    golden = _legacy_golden(pathlib.Path(args.legacy_root))
    evidence_path = EVIDENCE_DIR / f"chain-{n.ns}-evidence.json"
    evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else {}
    job = dbx.find_job(n.job_name)
    if not job:
        raise SystemExit(f"job {n.job_name} not found; run deploy first")
    settings = dbx.ok("GET", f"/api/2.1/jobs/get?job_id={job['job_id']}")["settings"]
    tasks = {t["task_key"]: t for t in settings["tasks"]}

    checks: list = []

    # orch-dag-dependencies
    graph = {
        key: sorted(d["task_key"] for d in task.get("depends_on", []))
        for key, task in tasks.items()
    }
    _check(
        checks,
        "orch-dag-dependencies",
        {
            "validate_params": [],
            "ingest": ["validate_params"],
            "parse": ["ingest"],
            "finance": ["parse"],
            "chain_complete": ["finance"],
            "chain_failed": ["finance", "ingest", "parse", "validate_params"],
        },
        graph,
        f"{dbx.host}/api/2.1/jobs/get job settings (live)",
    )

    # orch-max-active-runs
    _check(
        checks,
        "orch-max-active-runs",
        {
            "max_concurrent_runs": 1,
            "queue_enabled": True,
            "second_trigger_queued": True,
        },
        {
            "max_concurrent_runs": settings.get("max_concurrent_runs"),
            "queue_enabled": settings.get("queue", {}).get("enabled"),
            "second_trigger_queued": evidence.get("concurrency", {}).get(
                "second_queued"
            ),
        },
        "live job settings + observed life-cycle states of two back-to-back triggers",
    )

    # orch-failure-propagation
    failure = evidence.get("failure_run", {})
    live_failure = {}
    observed_attempts = None
    if failure.get("run_id"):
        failure_run_id = int(failure["run_id"])
        run = dbx.ok("GET", f"/api/2.1/jobs/runs/get?run_id={failure_run_id}")
        live_failure = _run_report(dbx, run)
        # a retried task reports the attempt it ended on: bounded retries were
        # not just configured, they actually ran
        observed_attempts = max(
            (
                t.get("attempt_number", 0)
                for t in run.get("tasks", [])
                if t["task_key"] == "parse"
            ),
            default=None,
        )
        ledger_rows = dbx.sql_ok(
            f"SELECT task_key, status FROM {n.ledger} "
            f"WHERE run_id = '{failure_run_id}' AND task_key = 'chain' ORDER BY recorded_at"
        ).rows
    else:
        ledger_rows = []
    _check(
        checks,
        "orch-failure-propagation",
        {
            "run_result_state": "FAILED",
            "parse": "FAILED",
            "finance_ran": False,
            "chain_ledger": [["chain", "failed"]],
        },
        {
            "run_result_state": live_failure.get("result_state"),
            "parse": live_failure.get("tasks", {}).get("parse"),
            "finance_ran": live_failure.get("tasks", {}).get("finance") in RAN_STATES,
            "chain_ledger": ledger_rows,
        },
        "live run state of the planted trailer-corruption run + run ledger table",
    )

    # orch-retries-and-alerting
    _check(
        checks,
        "orch-retries-and-alerting",
        {
            "stage_task_retries": {"ingest": 2, "parse": 2, "finance": 2},
            "failure_notification": True,
            "failure_recorded_in_ledger": True,
            "retries_observed_on_failed_task": 2,
        },
        {
            "stage_task_retries": {
                key: tasks[key].get("max_retries")
                for key in ("ingest", "parse", "finance")
            },
            "failure_notification": bool(
                settings.get("email_notifications", {}).get("on_failure")
            ),
            "failure_recorded_in_ledger": ledger_rows == [["chain", "failed"]],
            "retries_observed_on_failed_task": observed_attempts,
        },
        "live job settings + run ledger table + attempt numbers of the failed run",
    )

    # orch-end-to-end-parity
    landed = {
        row[0]: int(row[1])
        for row in dbx.sql_ok(
            f"SELECT source_file, max(file_size_bytes) FROM {n.bronze} "
            "GROUP BY source_file ORDER BY source_file"
        ).rows
    }
    silver_rows = int(dbx.sql_ok(f"SELECT count(*) FROM {n.silver}").scalar())
    gold_csv = _gold_csv(dbx, n)
    _check(
        checks,
        "orch-end-to-end-parity",
        {
            "landed_files": golden["landed_files"],
            "silver_rows": golden["silver_rows"],
            "finance_export_md5": golden["report_md5"],
        },
        {
            "landed_files": landed,
            "silver_rows": silver_rows,
            "finance_export_md5": hashlib.md5(gold_csv.encode()).hexdigest(),
        },
        f"golden legacy run under {args.legacy_root} vs live {n.bronze}/{n.silver}/{n.gold}",
    )

    # orch-idempotency
    rerun = evidence.get("idempotency", {})
    _check(
        checks,
        "orch-idempotency",
        {
            "result_state": "SUCCESS",
            "silver_rows_unchanged": True,
            "gold_export_unchanged": True,
        },
        {
            "result_state": rerun.get("result_state"),
            "silver_rows_unchanged": rerun.get("silver_rows_after")
            == rerun.get("silver_rows_before"),
            "gold_export_unchanged": rerun.get("gold_md5_after")
            == rerun.get("gold_md5_before"),
        },
        "second Run Now over the same input, counts and export md5 read back from the target",
    )

    # empty-input no-op semantics (contract ambiguity resolution, live proof)
    empty = evidence.get("empty_input", {})
    _check(
        checks,
        "orch-empty-input-noop",
        {
            "result_state": "SUCCESS",
            "silver_rows_unchanged": True,
            "gold_export_unchanged": True,
        },
        {
            "result_state": empty.get("result_state"),
            "silver_rows_unchanged": empty.get("silver_rows_after")
            == empty.get("silver_rows_before"),
            "gold_export_unchanged": empty.get("gold_md5_after")
            == empty.get("gold_md5_before"),
        },
        "Run Now over an emptied drop directory, counts and export md5 read back from the target",
    )

    # mandatory run parameter cannot fail open (self-check: NULL/missing attribution)
    missing = evidence.get("missing_param", {})
    _check(
        checks,
        "orch-mandatory-params",
        {
            "result_state": "FAILED",
            "validate_params": "FAILED",
            "ingest_ran": False,
        },
        {
            "result_state": missing.get("result_state"),
            "validate_params": missing.get("tasks", {}).get("validate_params"),
            "ingest_ran": missing.get("tasks", {}).get("ingest") in RAN_STATES,
        },
        "live run triggered with a blank ns run parameter",
    )

    # orch-schedule-paused
    _check(
        checks,
        "orch-schedule-paused",
        "PAUSED",
        settings.get("schedule", {}).get("pause_status"),
        "live job settings",
    )

    detected = []
    if evidence.get("concurrency", {}).get("second_queued"):
        detected.append("stage_overlap_race")
    if (
        live_failure.get("result_state") == "FAILED"
        and live_failure.get("tasks", {}).get("parse") == "FAILED"
    ):
        detected.append("silent_stage_failure")
    expected_anomalies = ["stage_overlap_race", "silent_stage_failure"]

    report = {
        "kind": "recon-report",
        "unit": "dbx-orchestrate",
        "namespace": n.ns,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_mode": args.run_mode,
        "source_artifacts": ["etl/legacy-extra/crontab", "etl/legacy-extra/run_all.sh"],
        "target_objects": [n.job_name, n.ledger, n.bronze, n.silver, n.gold],
        "golden_baseline": {
            "provenance": (
                "deterministic legacy run in this namespace: make legacy-etl-gen-data NS="
                f"{n.ns} then TP_FAKETIME='2026-01-15 00:00:00' scripts/tp-run-deterministic.sh "
                f"make legacy-etl-run JOB=run_all NS={n.ns}"
            ),
            "root": args.legacy_root,
            "report_path": golden["report_path"],
            "report_csv": golden["report_csv"],
            "landed_files": golden["landed_files"],
            "silver_rows": golden["silver_rows"],
        },
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            # derived from recorded evidence: a report must never claim a rerun
            # that was not actually triggered (the schema then rejects it)
            "performed": bool(rerun.get("run_id")),
            "result": "pass"
            if rerun.get("run_id")
            and all(
                c["result"] == "pass" for c in checks if c["id"] == "orch-idempotency"
            )
            else "fail",
            "evidence": json.dumps(rerun),
        },
        "planted_anomaly_detections": {
            "expected_set": expected_anomalies,
            "actual_set": detected,
            "missing": [a for a in expected_anomalies if a not in detected],
            "unexpected": [a for a in detected if a not in expected_anomalies],
        },
        "unverified_paths": args.unverified,
        "recon_result": "green"
        if all(c["result"] == "pass" for c in checks)
        else "red",
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"recon report -> {out} ({report['recon_result']})")
    for check in checks:
        print(f"  {check['result']:>4}  {check['id']}")
    return 0 if report["recon_result"] == "green" else 1


def cmd_idempotency(dbx: Databricks, n: ChainNames, args) -> int:
    before_rows = int(dbx.sql_ok(f"SELECT count(*) FROM {n.silver}").scalar())
    before_md5 = hashlib.md5(_gold_csv(dbx, n).encode()).hexdigest()
    run_id = _trigger(dbx, n)
    report = _run_report(dbx, dbx.wait_run(run_id))
    after_rows = int(dbx.sql_ok(f"SELECT count(*) FROM {n.silver}").scalar())
    after_md5 = hashlib.md5(_gold_csv(dbx, n).encode()).hexdigest()
    evidence = {
        **report,
        "silver_rows_before": before_rows,
        "silver_rows_after": after_rows,
        "gold_md5_before": before_md5,
        "gold_md5_after": after_md5,
    }
    print(json.dumps(evidence, indent=2))
    _record_evidence(n, "idempotency", evidence)
    return 0 if report["result_state"] == "SUCCESS" else 1


def cmd_state(dbx: Databricks, n: ChainNames, args) -> int:
    print(
        json.dumps(
            {
                "drop": [
                    e["path"]
                    for e in dbx.list_dir(n.drop_dir)
                    if not e.get("is_directory")
                ],
                "bronze": dbx.sql_ok(
                    f"SELECT source_file, count(*) AS lines, max(file_size_bytes) AS bytes "
                    f"FROM {n.bronze} GROUP BY source_file ORDER BY source_file"
                ).dicts(),
                "silver_rows": dbx.sql_ok(f"SELECT count(*) FROM {n.silver}").scalar(),
                "gold": dbx.sql_ok(
                    f"SELECT currency, record_type, record_count, total_amount_cents "
                    f"FROM {n.gold} ORDER BY currency, record_type"
                ).dicts(),
                "ledger": dbx.sql_ok(
                    f"SELECT run_id, task_key, status, detail FROM {n.ledger} "
                    "ORDER BY recorded_at DESC LIMIT 12"
                ).dicts(),
            },
            indent=2,
        )
    )
    return 0


COMMANDS = {
    "deploy": cmd_deploy,
    "land": cmd_land,
    "clear-drop": cmd_clear_drop,
    "run": cmd_run,
    "idempotency": cmd_idempotency,
    "concurrency": cmd_concurrency,
    "fail-test": cmd_fail_test,
    "state": cmd_state,
    "empty-input": cmd_empty_input,
    "missing-param": cmd_missing_param,
    "recon": cmd_recon,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--ns", required=True)
    parser.add_argument("--catalog", default="ow_tp")
    parser.add_argument("--drop-dir", default="")
    parser.add_argument("--legacy-root", default="")
    parser.add_argument(
        "--ns-param",
        default=None,
        help="override the ns run parameter (used to prove mandatory params)",
    )
    parser.add_argument("--evidence-key", default="")
    parser.add_argument("--run-mode", choices=["live", "fixture"], default="live")
    parser.add_argument("--unverified", nargs="*", default=list(UNVERIFIED_PATHS))
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    ns = require_ns(args.ns)
    n = ChainNames(ns=ns, catalog=args.catalog)
    if not args.legacy_root:
        args.legacy_root = f"/tmp/otterworks-legacy-{ns}"
    if not args.drop_dir:
        args.drop_dir = f"{args.legacy_root}/sftp-drop/upload"
    if not args.out:
        args.out = f"docs/tech-partnerships/recon/orchestrate-{ns}.recon.json"

    dbx = Databricks()
    try:
        return COMMANDS[args.command](dbx, n, args)
    except DbxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

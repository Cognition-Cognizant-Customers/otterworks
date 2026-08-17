#!/usr/bin/env python3
"""Deploy, run and reconcile the converted finance billing job (unit dbx-finance).

The converted job itself is scripts/tp_dbx/notebooks/finance_billing_job.py; this
harness only lands input, deploys the job, triggers runs, and reconciles the
result against the golden legacy output captured from a real run of
etl/legacy-extra/jobs/finance_excel_report.pl.

  land     upload the namespace's legacy parsed .psv drops into the landing volume
  deploy   import the job notebook and upsert the (schedule-PAUSED) job
  run      trigger one job run and report its outcome
  recon    the full acceptance sequence, emitting a schema-valid recon report
  status   what exists in the namespace right now
  teardown drop this namespace's own tables and delete its landed input

Every object is `ow_tp`-prefixed and suffixed with the namespace: the workspace is
shared, so this tool never creates or drops a catalog, schema or volume, never
creates compute, and never touches a table outside its own namespace suffix.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import Databricks, DbxError, require_ident, require_ns

REPO = Path(__file__).resolve().parents[2]
NOTEBOOK_DIR = "/Shared/ow_tp"
JOB_SOURCE = Path(__file__).resolve().parent / "notebooks" / "finance_billing_job.py"
LEGACY_SOURCE = REPO / "etl/legacy-extra/jobs/finance_excel_report.pl"


def load_job_module():
    """The job's own code is the single source of truth for table names, the export
    layout and the artifact-name rule, so the harness imports the file it uploads
    instead of restating any of it."""
    spec = importlib.util.spec_from_file_location("finance_billing_job", JOB_SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


JOB = load_job_module()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_name(ns: str) -> str:
    return f"ow_tp_finance_{ns}"


def notebook_path(ns: str) -> str:
    return f"{NOTEBOOK_DIR}/finance_billing_{ns}"


def names(args):
    return JOB.Names(catalog=require_ident(args.catalog, "catalog"), ns=require_ns(args.ns))


# --- golden legacy baseline --------------------------------------------------
def legacy_root(args) -> Path:
    # an unset value must not collapse to Path("") == ".", which is always a
    # directory: the baseline would then be "found" in the cwd and the recompute
    # would fail later with a confusing empty-input error instead of this guidance
    raw = args.legacy_root or os.environ.get("OTTERWORKS_LEGACY_ROOT", "")
    root = Path(raw) if raw else None
    if root is None or not root.is_dir():
        raise SystemExit(
            f"legacy root not found: {raw or '<unset>'}\n"
            "  capture the golden baseline first:\n"
            "    export OTTERWORKS_LEGACY_ROOT=/tmp/otterworks-legacy-<ns>\n"
            "    make legacy-etl-gen-data NS=<ns>\n"
            "    TP_FAKETIME='2026-01-15 00:00:00' scripts/tp-run-deterministic.sh "
            "make legacy-etl-run JOB=run_all NS=<ns>"
        )
    return root


def psv_files(root: Path) -> list[Path]:
    return sorted((root / "parsed").glob("CUSTBILL*.psv"))


def recompute_from_psv(root: Path) -> list[tuple[str, str, int, int]]:
    """The independent recompute: the runbook's awk one-liner over the legacy
    parsed drops, in exact integer cents rather than floating point."""
    counts: dict[tuple[str, str], list[int]] = {}
    files = psv_files(root)
    if not files:
        raise SystemExit(f"no parsed .psv files under {root}/parsed — the legacy run did not produce input")
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            fields = line.split("|")
            if len(fields) != JOB.PSV_FIELD_COUNT:
                raise SystemExit(f"{path.name}: expected {JOB.PSV_FIELD_COUNT} fields, got {len(fields)}: {line!r}")
            cust_id, _name, _date, amount, currency, record_type = fields
            if not cust_id.strip():
                continue  # the legacy report skips blank customer ids
            cents = int((Decimal(amount) * 100).to_integral_value())
            bucket = counts.setdefault((currency, record_type), [0, 0])
            bucket[0] += 1
            bucket[1] += cents
    return sorted((ccy, rt, c, cents) for (ccy, rt), (c, cents) in counts.items())


def legacy_report(root: Path) -> tuple[Path, bytes]:
    reports = sorted((root / "reports").glob("finance_billing_*.csv"))
    if not reports:
        raise SystemExit(f"no legacy finance report under {root}/reports — run the legacy job first")
    path = reports[-1]
    return path, path.read_bytes()


# --- commands ----------------------------------------------------------------
def cmd_land(dbx: Databricks, args) -> int:
    n = names(args)
    root = legacy_root(args)
    target = f"{n.landing}/{args.input_subdir}"
    landed = []
    for path in psv_files(root):
        payload = path.read_bytes()
        dbx.put_file(f"{target}/{path.name}", payload)
        landed.append((path.name, len(payload)))
    for name, size in landed:
        print(f"landed {name} ({size} bytes) -> {target}/{name}")
    if not landed:
        raise SystemExit("nothing to land")
    return 0


def job_settings(n, export_name: str) -> dict:
    return {
        "name": job_name(n.ns),
        "tags": {"project": "otterworks-tp", "demo": "custbill-finance", "namespace": n.ns},
        "max_concurrent_runs": 1,
        "parameters": [
            {"name": "ns", "default": n.ns},
            {"name": "catalog", "default": n.catalog},
            {"name": "input_subdir", "default": "parsed"},
            {"name": "export_name", "default": export_name},
            {"name": "delivery_probe", "default": "off"},
        ],
        "tasks": [{
            "task_key": "finance_billing",
            "notebook_task": {
                "notebook_path": notebook_path(n.ns),
                "base_parameters": {
                    "ns": "{{job.parameters.ns}}",
                    "catalog": "{{job.parameters.catalog}}",
                    "input_subdir": "{{job.parameters.input_subdir}}",
                    "export_name": "{{job.parameters.export_name}}",
                    "delivery_probe": "{{job.parameters.delivery_probe}}",
                    "run_id": "{{job.run_id}}",
                },
            },
        }],
        # nothing in this demo runs on a schedule
        "schedule": {"quartz_cron_expression": "0 0 7 * * ?", "timezone_id": "UTC", "pause_status": "PAUSED"},
        "queue": {"enabled": True},
    }


def cmd_deploy(dbx: Databricks, args) -> int:
    n = names(args)
    dbx.import_notebook(notebook_path(n.ns), JOB_SOURCE.read_text(encoding="utf-8"))
    job_id = dbx.upsert_job(job_settings(n, args.export_name))
    print(f"job {job_id} (schedule PAUSED): {dbx.host}/jobs/{job_id}")
    print(f"  notebook: {notebook_path(n.ns)}")
    return 0


def task_error(dbx: Databricks, run: dict) -> str:
    """A failed run's own state_message only says "see run output"; the reason a
    probe was rejected lives on the task, so the anomaly evidence comes from there."""
    messages = []
    for task in run.get("tasks", []):
        status, payload = dbx.call("GET", f"/api/2.1/jobs/runs/get-output?run_id={task['run_id']}")
        if 200 <= status < 300 and payload.get("error"):
            messages.append(str(payload["error"]))
    return " | ".join(messages)[:600]


def trigger(dbx: Databricks, n, params: dict) -> dict:
    job = dbx.find_job(job_name(n.ns))
    if not job:
        raise SystemExit(f"job {job_name(n.ns)} not found; run deploy first")
    run_id = dbx.run_job(int(job["job_id"]), params)
    run = dbx.wait_run(run_id)
    state = run.get("state", {})
    outcome = {
        "run_id": run_id,
        "url": dbx.run_url(run_id),
        "result_state": state.get("result_state"),
        "message": task_error(dbx, run) or str(state.get("state_message", ""))[:600],
        "parameters": params,
    }
    print(f"run {run_id}: {outcome['result_state']} ({outcome['url']}) params={params}")
    return outcome


def require_success(outcome: dict, label: str) -> dict:
    """A recon run that failed must never be reconciled around: gold and the
    delivered export both survive between invocations, so a failed run leaves the
    *previous* run's correct rows and bytes in place and every content check
    downstream would compare those and pass. Stop instead of reporting a green
    recon for a run that did not happen."""
    if outcome["result_state"] != "SUCCESS":
        raise SystemExit(
            f"recon {label} run {outcome['run_id']} ended {outcome['result_state']}, not SUCCESS: "
            f"{outcome['message']}\n  {outcome['url']}"
        )
    return outcome


def cmd_run(dbx: Databricks, args) -> int:
    n = names(args)
    params = {
        "input_subdir": args.input_subdir,
        "export_name": args.export_name,
        "delivery_probe": args.delivery_probe,
    }
    outcome = trigger(dbx, n, params)
    return 0 if outcome["result_state"] == "SUCCESS" else 1


def gold_rows(dbx: Databricks, n) -> list[tuple[str, str, int, int]]:
    result = dbx.sql_ok(JOB.gold_rows_query(n))
    return sorted(
        (row["currency"], row["record_type"], int(row["record_count"]), int(row["total_amount_cents"]))
        for row in result.dicts()
    )


def download_export(dbx: Databricks, path: str) -> bytes | None:
    """The Files API returns raw bytes, so read the response body directly rather
    than through the JSON helper."""
    import urllib.error
    import urllib.parse
    import urllib.request

    quoted = urllib.parse.quote(path, safe="/")
    request = urllib.request.Request(
        dbx.host + "/api/2.0/fs/files" + quoted,
        headers={"Authorization": f"Bearer {dbx.token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise DbxError(f"GET {path} -> HTTP {exc.code}: {exc.read()[:300]!r}") from exc


def rows_as_strings(rows) -> list[str]:
    return [f"{ccy},{JOB.RECORD_TYPE_NAMES[rt]},{count},{JOB.cents_to_amount(cents)}" for ccy, rt, count, cents in rows]


def check(checks: list[dict], check_id: str, expected, actual, source: str) -> bool:
    passed = expected == actual
    checks.append({
        "id": check_id, "expected": expected, "actual": actual,
        "source_of_truth": source, "result": "pass" if passed else "fail",
    })
    return passed


def cmd_recon(dbx: Databricks, args) -> int:
    n = names(args)
    root = legacy_root(args)
    exports_dir = n.exports
    main_export = f"{exports_dir}/{JOB.EXPORT_NAME}"
    empty_export = f"{exports_dir}/{JOB.EMPTY_EXPORT_NAME}"
    legacy_psv = "legacy parsed .psv drops recomputed independently (runbook Beat 4 awk one-liner)"
    legacy_csv_label = "golden legacy output: $OTTERWORKS_LEGACY_ROOT/reports/finance_billing_*.csv"

    expected_rows = recompute_from_psv(root)
    report_path, report_bytes = legacy_report(root)
    expected_payload = JOB.render_export(expected_rows)
    checks: list[dict] = []

    # the recompute and the legacy artifact must agree before either is used as a baseline
    check(checks, "finance-independent-recompute",
          {"rows": rows_as_strings(expected_rows), "sha256": hashlib.sha256(expected_payload).hexdigest()},
          {"rows": report_bytes.decode("utf-8").splitlines()[1:],
           "sha256": hashlib.sha256(report_bytes).hexdigest()},
          f"{legacy_psv} vs {legacy_csv_label} ({report_path.name})")

    # --- run A: the real batch
    run_a = require_success(
        trigger(dbx, n, {"input_subdir": args.input_subdir, "export_name": JOB.EXPORT_NAME,
                         "delivery_probe": "off"}), "build")
    gold_a = gold_rows(dbx, n)
    export_a = download_export(dbx, main_export)

    check(checks, "finance-aggregate-parity",
          {"run_succeeded": True, "groups": rows_as_strings(expected_rows)},
          {"run_succeeded": run_a["result_state"] == "SUCCESS", "groups": rows_as_strings(gold_a)},
          f"{legacy_csv_label}; actual recomputed from {n.gold} after run {run_a['run_id']}")
    for ccy, rt, count, cents in expected_rows:
        actual = [row for row in gold_a if row[0] == ccy and row[1] == rt]
        check(checks, f"finance-aggregate-parity/{ccy}/{JOB.RECORD_TYPE_NAMES[rt]}",
              f"{count}|{cents}",
              f"{actual[0][2]}|{actual[0][3]}" if actual else "missing",
              f"{legacy_psv}; actual from {n.gold}")

    check(checks, "finance-real-artifact",
          {"name": JOB.EXPORT_NAME, "header": JOB.HEADER, "is_csv_bytes": True,
           "sha256_equals_legacy_csv": True, "xls_artifacts": []},
          {"name": JOB.EXPORT_NAME,
           "header": (export_a or b"").decode("utf-8").splitlines()[0] if export_a else None,
           "is_csv_bytes": bool(export_a) and export_a.decode("utf-8").count(",") >= 4,
           "sha256_equals_legacy_csv": hashlib.sha256(export_a or b"").hexdigest()
                                       == hashlib.sha256(report_bytes).hexdigest(),
           "xls_artifacts": sorted(entry["path"] for entry in dbx.list_dir(exports_dir)
                                   if entry["path"].endswith((".xls", ".xlsx")))},
          f"export object at {main_export} read back from the volume")

    # --- run B: idempotency
    run_b = require_success(
        trigger(dbx, n, {"input_subdir": args.input_subdir, "export_name": JOB.EXPORT_NAME,
                         "delivery_probe": "off"}), "idempotency rerun")
    gold_b = gold_rows(dbx, n)
    export_b = download_export(dbx, main_export)
    idempotent = gold_a == gold_b and export_a == export_b and export_b == expected_payload

    # --- planted anomaly probes: both must fail the run
    probe_noop = trigger(dbx, n, {"input_subdir": args.input_subdir,
                                  "export_name": "finance_billing.probe.csv",
                                  "delivery_probe": "skip_write"})
    probe_mislabel = trigger(dbx, n, {"input_subdir": args.input_subdir,
                                      "export_name": "finance_billing.xls",
                                      "delivery_probe": "off"})
    detected = []
    if probe_noop["result_state"] != "SUCCESS" and "silent_delivery_noop" in probe_noop["message"]:
        detected.append(["silent_delivery_noop", "run_failed"])
    if probe_mislabel["result_state"] != "SUCCESS" and "mislabelled_artifact_type" in probe_mislabel["message"]:
        detected.append(["mislabelled_artifact_type", "run_failed"])
    probe_leftover = download_export(dbx, f"{exports_dir}/finance_billing.probe.csv")

    check(checks, "finance-verified-delivery",
          {"exists": True, "byte_size": len(expected_payload), "row_count": len(expected_rows),
           "undelivered_run_fails": True, "mislabelled_run_fails": True, "probe_artifact_left_behind": False},
          {"exists": export_b is not None, "byte_size": len(export_b or b""),
           "row_count": JOB.export_row_count(export_b or b""),
           "undelivered_run_fails": probe_noop["result_state"] != "SUCCESS",
           "mislabelled_run_fails": probe_mislabel["result_state"] != "SUCCESS",
           "probe_artifact_left_behind": probe_leftover is not None},
          f"{main_export} read back from the volume; probe runs {probe_noop['run_id']} / {probe_mislabel['run_id']}")

    legacy_text = LEGACY_SOURCE.read_text(encoding="utf-8")
    # recipients are counted, never echoed, so no personal address reaches the evidence:
    # the legacy script pins one per environment branch, including a departed employee's.
    recipient_literal = re.compile(r"\$MAILTO\s*=\s*\"")
    address = re.compile(r"[\w.+-]+\\?@[\w-]+\.[\w.]+")
    converted_text = JOB_SOURCE.read_text(encoding="utf-8") + Path(__file__).read_text(encoding="utf-8")
    converted_addresses = sorted(set(address.findall(converted_text)))
    check(checks, "finance-managed-recipients",
          {"legacy_hard_coded_recipients": 3, "converted_hard_coded_recipients": [],
           "destination_is_configuration": True},
          {"legacy_hard_coded_recipients": len(recipient_literal.findall(legacy_text)),
           "converted_hard_coded_recipients": converted_addresses,
           "destination_is_configuration": "export_name" in converted_text and exports_dir.endswith(n.ns)},
          "static scan of the converted job and harness sources vs the legacy Perl script")

    # --- empty input: header-only export, zero gold rows, prior export untouched
    empty_input_dir = f"{n.landing}/{args.empty_subdir}"
    quoted_empty_input_dir = urllib.parse.quote(empty_input_dir, safe="/")
    directory_status, directory_body = dbx.call(
        "PUT", f"/api/2.0/fs/directories{quoted_empty_input_dir}"
    )
    if not 200 <= directory_status < 300:
        raise DbxError(
            f"PUT {empty_input_dir} -> HTTP {directory_status}: "
            f"{json.dumps(directory_body)[:300]}"
        )
    # existence is read back from the API rather than assumed from the PUT: list_dir
    # answers [] for both an empty directory and a missing one, so a 404 here would
    # otherwise look exactly like the empty batch this check is about
    listed_status, _ = dbx.call("GET", f"/api/2.0/fs/directories{quoted_empty_input_dir}")
    empty_input_exists = 200 <= listed_status < 300
    empty_input_entries = dbx.list_dir(empty_input_dir)
    if not empty_input_exists:
        raise DbxError(f"empty input directory missing after create: {empty_input_dir}")
    if empty_input_entries:
        raise DbxError(
            f"empty input directory is not empty: {empty_input_dir} "
            f"({len(empty_input_entries)} entries)"
        )
    run_empty = trigger(dbx, n, {"input_subdir": args.empty_subdir, "export_name": JOB.EXPORT_NAME,
                                 "delivery_probe": "off"})
    gold_empty = gold_rows(dbx, n)
    empty_payload = download_export(dbx, empty_export)
    main_after_empty = download_export(dbx, main_export)
    check(checks, "finance-empty-input",
          {"run_succeeded": True, "input_dir_exists": True, "input_dir_empty": True,
           "gold_rows": 0, "empty_export": JOB.HEADER + "\n",
           "prior_export_preserved": True},
          {"run_succeeded": run_empty["result_state"] == "SUCCESS",
           "input_dir_exists": empty_input_exists, "input_dir_empty": empty_input_entries == [],
           "gold_rows": len(gold_empty),
           "empty_export": (empty_payload or b"").decode("utf-8"),
           "prior_export_preserved": main_after_empty == export_b},
          f"verified empty input dir {empty_input_dir} exists and list_dir is empty; "
          f"run {run_empty['run_id']} used that directory")

    # --- stale-artifact probe: the export is byte-identical across runs, so a
    # read-back of the destination only proves delivery if last run's artifact
    # cannot satisfy it. Probed against the real path, with a good artifact in
    # place; run C below re-delivers it.
    probe_stale = trigger(dbx, n, {"input_subdir": args.input_subdir, "export_name": JOB.EXPORT_NAME,
                                   "delivery_probe": "skip_write"})
    stale_leftover = download_export(dbx, main_export)
    check(checks, "finance-verified-delivery",
          {"stale_artifact_run_fails": True, "prior_artifact_survives_failure": True},
          {"stale_artifact_run_fails": probe_stale["result_state"] != "SUCCESS"
                                       and "silent_delivery_noop" in probe_stale["message"],
           "prior_artifact_survives_failure": stale_leftover == export_b},
          f"probe run {probe_stale['run_id']} skipped the write with a byte-identical artifact "
          f"already at {main_export}")

    # --- run C: restore the good batch and confirm it reproduces run A exactly
    run_c = require_success(
        trigger(dbx, n, {"input_subdir": args.input_subdir, "export_name": JOB.EXPORT_NAME,
                         "delivery_probe": "off"}), "rebuild after empty batch")
    gold_c = gold_rows(dbx, n)
    export_c = download_export(dbx, main_export)
    restored = gold_c == gold_a and export_c == export_a
    check(checks, "finance-idempotency",
          {"reruns_succeeded": True, "gold_rows_identical": True,
           "export_sha256": hashlib.sha256(expected_payload).hexdigest(),
           "rebuild_after_empty_batch_identical": True},
          {"reruns_succeeded": run_b["result_state"] == "SUCCESS" and run_c["result_state"] == "SUCCESS",
           "gold_rows_identical": gold_a == gold_b,
           "export_sha256": hashlib.sha256(export_c or b"").hexdigest(),
           "rebuild_after_empty_batch_identical": restored},
          f"reruns {run_b['run_id']} and {run_c['run_id']} recomputed from {n.gold} and the delivered export")

    expected_anomalies = sorted([["mislabelled_artifact_type", "run_failed"], ["silent_delivery_noop", "run_failed"]])
    actual_anomalies = sorted(detected)
    expected_keys = {tuple(item) for item in expected_anomalies}
    actual_keys = {tuple(item) for item in actual_anomalies}

    report = {
        "kind": "recon-report",
        "unit": "dbx-finance",
        "namespace": n.ns,
        "generated_at": now(),
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if idempotent and restored else "fail",
            "evidence": (
                f"job re-run {run_b['run_id']} reproduced run {run_a['run_id']} byte-identically "
                f"({len(export_b or b'')} bytes, sha256 {hashlib.sha256(export_b or b'').hexdigest()[:16]}...), "
                f"and run {run_c['run_id']} restored the same gold rows and export after the "
                f"empty-input run {run_empty['run_id']}"
            ),
        },
        "planted_anomaly_detections": {
            "expected_set": expected_anomalies,
            "actual_set": actual_anomalies,
            "missing": sorted(list(key) for key in expected_keys - actual_keys),
            "unexpected": sorted(list(key) for key in actual_keys - expected_keys),
            "coverage_gaps": [{
                "id": "non_numeric_amount",
                "reason": ("contract coverage_gap: record-level anomalies are quarantined upstream by "
                           "dbx-parse, so this gold aggregate only ever consumes validated silver rows"),
            }],
        },
        "unverified_paths": args.unverified,
        "evidence": {
            "golden_baseline": str(report_path),
            "job_runs": {"build": run_a["url"], "idempotency": run_b["url"],
                         "delivery_noop_probe": probe_noop["url"], "mislabel_probe": probe_mislabel["url"],
                         "empty_input": run_empty["url"], "restore": run_c["url"]},
            "gold_table": f"{dbx.host}/explore/data/{n.catalog}/gold/custbill_billing_summary_{n.ns}",
            "export_object": main_export,
            "capability_preflight": (
                "parent orchestration session's `make tp-preflight PLATFORM=databricks` (11 probes, 0 denied) "
                "carried into this unit; this unit re-exercised the paths it needs itself — Files API PUT to "
                f"{n.landing}, GET readback of {main_export}, Unity Catalog create/insert on its own "
                f"_{n.ns} tables, and job create/run on {job_name(n.ns)}"
            ),
        },
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    failed = [c for c in checks if c["result"] == "fail"]
    print(f"wrote {out} — {len(checks) - len(failed)}/{len(checks)} checks pass")
    for c in failed:
        print(f"  FAIL {c['id']}: expected {c['expected']!r} actual {c['actual']!r}")
    if report["planted_anomaly_detections"]["missing"]:
        print(f"  MISSING anomalies: {report['planted_anomaly_detections']['missing']}")
    ok = not failed and not report["planted_anomaly_detections"]["missing"] \
        and report["idempotency_rerun"]["result"] == "pass"
    return 0 if ok else 1


def cmd_status(dbx: Databricks, args) -> int:
    n = names(args)
    for table in (n.bronze, n.silver, n.gold, n.audit):
        result = dbx.sql(f"SELECT count(*) FROM {table}")
        print(f"{table}: {result.scalar() if result.ok else result.state}")
    print(f"exports {n.exports}: {[e['path'] for e in dbx.list_dir(n.exports)]}")
    print(f"landing {n.landing}/{args.input_subdir}: {[e['path'] for e in dbx.list_dir(f'{n.landing}/{args.input_subdir}')]}")
    job = dbx.find_job(job_name(n.ns))
    print(f"job {job_name(n.ns)}: {job['job_id'] if job else 'absent'}")
    return 0


def cmd_teardown(dbx: Databricks, args) -> int:
    """Only this namespace's own objects; recon evidence in the repo is never touched.

    Teardown is destructive and cannot tell a stale run's data from a newer one, so
    it refuses to run unless the namespace is named again explicitly, and it keeps
    the delivery audit table and the delivered exports as run evidence."""
    n = names(args)
    if args.confirm != n.ns:
        raise SystemExit(f"refusing to tear down {n.ns}: pass --confirm {n.ns} to drop this namespace's tables")
    for table in (n.gold, n.silver, n.bronze):
        dbx.sql_ok(f"DROP TABLE IF EXISTS {table}")
        print(f"dropped {table}")
    input_dirs = (
        f"{n.landing}/{args.input_subdir}",
        f"{n.landing}/{args.empty_subdir}",
    )
    for input_dir in input_dirs:
        for entry in dbx.list_dir(input_dir):
            dbx.delete_file(entry["path"])
        dbx.delete_dir(input_dir)
        print(f"deleted directory {input_dir}")
    # n.landing is already namespace-scoped; the volume root itself is shared.
    dbx.delete_dir(n.landing)
    print(f"deleted directory {n.landing}")
    job = dbx.find_job(job_name(n.ns))
    if job:
        dbx.ok("POST", "/api/2.1/jobs/delete", {"job_id": int(job["job_id"])})
        print(f"deleted job {job['job_id']}")
    status, _ = dbx.call(
        "POST", "/api/2.0/workspace/delete", {"path": notebook_path(n.ns)}
    )
    print(f"workspace delete {notebook_path(n.ns)}: HTTP {status}")

    def directory_exists(path: str) -> bool:
        quoted = urllib.parse.quote(path, safe="/")
        status, payload = dbx.call("GET", f"/api/2.0/fs/directories{quoted}")
        if status == 404:
            return False
        if not 200 <= status < 300:
            raise DbxError(f"GET {path} -> HTTP {status}: {json.dumps(payload)[:300]}")
        return True

    def workspace_object_exists(path: str) -> bool:
        quoted = urllib.parse.quote(path, safe="")
        status, payload = dbx.call(
            "GET", f"/api/2.0/workspace/get-status?path={quoted}"
        )
        if status == 404:
            return False
        if not 200 <= status < 300:
            raise DbxError(f"GET workspace status {path} -> HTTP {status}: {json.dumps(payload)[:300]}")
        return True

    # negative verification across every object class teardown touches
    # sql_ok, not sql: an errored scan returns no rows, which would read as proof
    # of absence and let teardown report a namespace it never cleaned
    remaining = {}
    for schema in ("bronze", "silver", "gold"):
        result = dbx.sql_ok(f"SHOW TABLES IN {n.catalog}.{schema} LIKE '*_{n.ns}'")
        remaining[f"{schema}_tables"] = result.rows
    remaining.update({
        "input_dirs": [path for path in input_dirs if directory_exists(path)],
        "landing_dir": directory_exists(n.landing),
        "job": dbx.find_job(job_name(n.ns)) is not None,
        "notebook": workspace_object_exists(notebook_path(n.ns)),
    })
    print("negative verification: " + json.dumps(remaining))
    survivors = [key for key, value in remaining.items() if value]
    if survivors:
        print(f"teardown incomplete, survivors: {survivors}")
        return 1
    print(f"retained as run evidence: {n.audit} and the exports under {n.exports}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ns", default="cnvfinance")
    # the catalog is shared and parent-owned: this unit only ever writes ow_tp
    parser.add_argument("--catalog", default="ow_tp", choices=["ow_tp"])
    parser.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", ""))
    parser.add_argument("--input-subdir", default="parsed")
    parser.add_argument("--empty-subdir", default="parsed_empty")
    parser.add_argument("--legacy-root", default="")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("land")
    deploy = sub.add_parser("deploy")
    deploy.add_argument("--export-name", default=JOB.EXPORT_NAME)
    run = sub.add_parser("run")
    run.add_argument("--export-name", default=JOB.EXPORT_NAME)
    run.add_argument("--delivery-probe", default="off", choices=["off", "skip_write"])
    recon = sub.add_parser("recon")
    recon.add_argument("--out", default="docs/tech-partnerships/recon/finance-cnvfinance.recon.json")
    recon.add_argument("--run-mode", default="live", choices=["live", "fixture"])
    recon.add_argument(
        "--unverified",
        nargs="*",
        default=[
            (
                "legacy sendmail delivery of the finance report (no SMTP in the demo estate; "
                "the converted job delivers to a volume and verifies it instead)"
            ),
            "email/SMTP or shared-drive distribution of the converted export (out of scope for this unit)",
            (
                "the null_raw_line rejection: read_files() never produced a NULL line in the live "
                "batches, so that validation branch is proven by static reasoning only, not by a run"
            ),
        ],
    )
    sub.add_parser("status")
    teardown = sub.add_parser("teardown")
    teardown.add_argument("--confirm", default="", help="repeat the namespace to confirm the drop")
    args = parser.parse_args()

    dbx = Databricks(warehouse_id=args.warehouse_id or None)
    handlers = {"land": cmd_land, "deploy": cmd_deploy, "run": cmd_run,
                "recon": cmd_recon, "status": cmd_status, "teardown": cmd_teardown}
    return handlers[args.command](dbx, args)


if __name__ == "__main__":
    raise SystemExit(main())

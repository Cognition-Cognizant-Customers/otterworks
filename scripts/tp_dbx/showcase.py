#!/usr/bin/env python3
"""Stand up, backfill, reconcile and showcase the OtterWorks billing history on Databricks.

Everything is namespace-scoped (`--ns`) and `ow_tp`-prefixed: the demo workspace
is shared, so this tool never runs DDL on a table another namespace can see and
never creates clusters (serverless SQL warehouse only).

  provision     catalog/schemas/volume/tables for the namespace
  land          upload generated CUSTBILL history drops into the landing volume
  expectations  load the generator's legacy-derived expectations
  backfill      bronze -> silver + quarantine -> gold (full or one period)
  recon         recompute from the target, emit a schema-valid recon report
  timetravel    Delta history + as-of totals evidence
  lineage       Unity Catalog lineage evidence for the migrated tables
  dashboard     create/refresh the AI-BI dashboard for the finance totals
  alert         create the (paused) recon SQL alert
  pipeline      create/refresh the Lakeflow declarative pipeline (declared expectations)
  run-pipeline  trigger the pipeline, report expectation metrics and harness parity
  recon-job     create/refresh the recon job with the failure -> Devin task
  run-job       trigger the recon job and report the run URL and outcome
  drift         stage the demo failure beat (new history arrives, target stale)
  status        summarise what exists in the namespace
  teardown      drop the namespace's objects and landed files
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sql as S
from client import Databricks, DbxError, require_ident, require_ns

REPO = Path(__file__).resolve().parents[2]
NOTEBOOK_DIR = "/Shared/ow_tp"


def names(args) -> S.Names:
    return S.Names(catalog=require_ident(args.catalog, "catalog"), ns=require_ns(args.ns))


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def esc(value: str) -> str:
    """Databricks string literals honour backslash escapes, so a value ending in a
    backslash would otherwise neutralise the closing quote."""
    return value.replace("\\", "\\\\").replace("'", "''")


def find_alert(dbx: Databricks, name: str) -> dict | None:
    # the alerts API returns the page under `alerts`; older docs say `results`
    listed = dbx.ok("GET", "/api/2.0/alerts?page_size=100")
    for alert in listed.get("alerts", listed.get("results", [])):
        if alert.get("display_name") == name and alert.get("lifecycle_state") == "ACTIVE":
            return alert
    return None


def as_int(value) -> int:
    """Manifest numerics land in SQL literals unquoted, and --expectations-file
    means the manifest is not necessarily one we generated."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SystemExit(f"expectations manifest carried a non-integer numeric: {value!r}")
    return value


def manifest_path(args) -> Path:
    if args.expectations_file:
        return Path(args.expectations_file)
    root = Path(args.legacy_root)
    return root / "sftp-drop/history/expected" / f"{args.ns}-history-expected.json"


def load_manifest(args) -> dict:
    path = manifest_path(args)
    if not path.exists():
        raise SystemExit(
            f"expectations manifest not found: {path}\n"
            f"  generate it first: make legacy-etl-gen-history NS={args.ns}"
        )
    return json.loads(path.read_text())


# --- commands ---------------------------------------------------------------
def cmd_provision(dbx: Databricks, args) -> int:
    n = names(args)
    for statement in S.provision(n):
        dbx.sql_ok(statement)
    print(f"provisioned {n.catalog} (bronze/silver/gold/ops) for ns={n.ns}")
    print(f"landing volume: {n.landing}")
    return 0


def cmd_land(dbx: Databricks, args) -> int:
    n = names(args)
    root = Path(args.legacy_root) / "sftp-drop/history"
    if not root.exists():
        raise SystemExit(f"no generated history at {root}; run make legacy-etl-gen-history NS={n.ns}")
    # the generator writes every namespace into the same legacy root, so scope
    # the upload to this namespace's drops or a sibling demo's history bleeds in
    files = sorted(p for p in root.rglob(f"CUSTBILL_{n.ns.upper()}_*.dat") if p.is_file())
    if args.period:
        files = [p for p in files if p.name.endswith(f"_{args.period}.dat")]
    if not files:
        raise SystemExit("no CUSTBILL history files matched")
    landed = 0
    for path in files:
        year = path.parent.name
        target = f"{n.history_dir}/{year}/{path.name}"
        dbx.put_file(target, path.read_bytes())
        landed += 1
    print(f"landed {landed} CUSTBILL drops under {n.history_dir}")
    return 0


def cmd_expectations(dbx: Databricks, args) -> int:
    n = names(args)
    data = load_manifest(args)
    files_per_year: dict[int, int] = {}
    for entry in data["files"]:
        files_per_year[entry["year"]] = files_per_year.get(entry["year"], 0) + 1
    rows = []
    for year_block in data["per_year"]:
        year = year_block["year"]
        for total in year_block["totals"]:
            rows.append(
                f"({as_int(year)}, '{esc(total['currency'])}', '{esc(total['record_type'])}', "
                f"{as_int(total['record_count'])}, {as_int(total['total_amount_cents'])}, "
                f"{as_int(year_block['quarantine_record_count'])}, "
                f"{as_int(files_per_year.get(year, 0))})"
            )
    if not rows:
        raise SystemExit("expectations manifest carried no per-year totals")
    dbx.sql_ok(
        f"INSERT OVERWRITE {n.expectations} (source_year, currency, record_type, record_count, "
        f"total_amount_cents, quarantine_record_count, file_count) VALUES\n" + ",\n".join(rows)
    )
    print(f"loaded {len(rows)} expectation rows for years "
          f"{data['start_year']}-{data['end_year']} into {n.expectations}")
    return 0


def cmd_backfill(dbx: Databricks, args) -> int:
    n = names(args)
    if args.period:
        period = args.period
        if not (len(period) == 6 and period.isdigit()):
            raise SystemExit("--period must be YYYYMM")
        year = period[:4]
        dbx.sql_ok(S.delete_bronze_period(n, period))
        dbx.sql_ok(S.load_bronze(n, f"{n.history_dir}/{year}/CUSTBILL_*_{period}.dat", overwrite=False))
        print(f"incremental load of period {period} into {n.bronze}")
    else:
        dbx.sql_ok(S.load_bronze(n, n.history_dir, overwrite=True))
        print(f"full historical load into {n.bronze}")
    dbx.sql_ok(S.build_silver(n))
    dbx.sql_ok(S.build_quarantine(n))
    dbx.sql_ok(S.build_gold(n))
    summary = dbx.sql_ok(
        f"SELECT (SELECT count(*) FROM {n.bronze}) AS bronze_rows, "
        f"(SELECT count(DISTINCT source_file) FROM {n.bronze}) AS files, "
        f"(SELECT count(*) FROM {n.silver}) AS silver_rows, "
        f"(SELECT count(*) FROM {n.quarantine}) AS quarantined, "
        f"(SELECT count(*) FROM {n.gold}) AS gold_rows, "
        f"(SELECT min(source_year) FROM {n.bronze}) AS first_year, "
        f"(SELECT max(source_year) FROM {n.bronze}) AS last_year"
    )
    print(json.dumps(summary.dicts()[0], indent=2))
    return 0


def _checks(dbx: Databricks, n: S.Names) -> list[dict]:
    return dbx.sql_ok(S.recon_checks(n)).dicts()


def cmd_recon(dbx: Databricks, args) -> int:
    n = names(args)
    data = load_manifest(args)
    run_id = uuid.uuid4().hex[:12]
    checks = _checks(dbx, n)

    # the report is audit evidence and its schema pins performed to true, so the
    # rerun is not optional: there is no honest way to emit a skipped one
    dbx.sql_ok(S.build_silver(n))
    dbx.sql_ok(S.build_quarantine(n))
    dbx.sql_ok(S.build_gold(n))
    rerun = _checks(dbx, n)
    same = rerun == checks
    idempotency = {
        "performed": True,
        "result": "pass" if same else "fail",
        "evidence": ("silver/quarantine/gold rebuilt from bronze; all "
                     f"{len(checks)} checks byte-identical" if same
                     else "check values changed on rerun"),
    }
    checks = rerun

    expected_anomalies = sorted(
        [a["file"], a["kind"], a["cust_id"]] for a in data["planted_anomalies"]
    )
    actual_anomalies = sorted(
        [row["source_file"], row["reason"], row["cust_id"]]
        for row in dbx.sql_ok(S.anomaly_set(n)).dicts()
    )
    expected_keys = {tuple(a) for a in expected_anomalies}
    actual_keys = {tuple(a) for a in actual_anomalies}

    report = {
        "kind": "recon-report",
        "unit": "custbill_history_backfill",
        "namespace": n.ns,
        "generated_at": now(),
        "run_mode": "live",
        "checks": [
            {
                "id": c["check_id"],
                "expected": c["expected"],
                "actual": c["actual"],
                "source_of_truth": "gen_history_data.pl expectations manifest (legacy fixed-width drops)",
                "result": c["result"],
            }
            for c in checks
        ],
        "values_recomputed_from_target": True,
        "idempotency_rerun": idempotency,
        "planted_anomaly_detections": {
            "expected_set": expected_anomalies,
            "actual_set": actual_anomalies,
            "missing": sorted(list(k) for k in expected_keys - actual_keys),
            "unexpected": sorted(list(k) for k in actual_keys - expected_keys),
        },
        "unverified_paths": [
            "legacy sendmail delivery of the finance report (no SMTP in the demo estate)",
        ],
    }
    failed = [c for c in report["checks"] if c["result"] == "fail"]
    rows = ",".join(
        f"('{run_id}', current_timestamp(), '{esc(c['id'])}', '{esc(str(c['expected']))}', "
        f"'{esc(str(c['actual']))}', '{esc(c['result'])}')"
        for c in report["checks"]
    )
    dbx.sql_ok(f"INSERT INTO {n.recon_runs} VALUES {rows}")

    out = Path(args.out) if args.out else REPO / f"docs/tech-partnerships/recon/custbill_history_backfill-{n.ns}.recon.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"wrote {out}")
    print(f"checks: {len(report['checks'])}, failed: {len(failed)}, "
          f"anomalies expected/actual: {len(expected_anomalies)}/{len(actual_anomalies)}, "
          f"missing: {len(report['planted_anomaly_detections']['missing'])}, "
          f"unexpected: {len(report['planted_anomaly_detections']['unexpected'])}")
    for c in failed[:10]:
        print(f"  FAIL {c['id']} expected={c['expected']} actual={c['actual']}")
    if failed or idempotency["result"] == "fail":
        return 1
    if report["planted_anomaly_detections"]["missing"] or report["planted_anomaly_detections"]["unexpected"]:
        return 1
    return 0


def cmd_timetravel(dbx: Databricks, args) -> int:
    n = names(args)
    table = {"gold": n.gold, "silver": n.silver, "bronze": n.bronze}[args.table]
    history = dbx.sql_ok(S.describe_history(n, table))
    versions = [dict(zip(history.columns, row)) for row in history.rows]
    print(f"{table}: {len(versions)} Delta versions")
    for entry in versions[: args.limit]:
        print(f"  v{entry['version']} {entry['timestamp']} {entry['operation']}")
    if len(versions) >= 2:
        newest = int(versions[0]["version"])
        previous = int(versions[1]["version"])
        for version in (previous, newest):
            totals = dbx.sql_ok(S.timetravel_totals(n, table, version)).dicts()[0]
            print(f"  totals AS OF v{version}: {totals}")
    print(f"  restore command (not run): RESTORE TABLE {table} TO VERSION AS OF <version>")
    return 0


def cmd_lineage(dbx: Databricks, args) -> int:
    n = names(args)
    for table in (n.bronze, n.silver, n.gold):
        status, payload = dbx.call(
            "GET", "/api/2.0/lineage-tracking/table-lineage?table_name=" + table + "&include_entity_lineage=true"
        )
        upstreams = [
            u.get("tableInfo", {}).get("name") or u.get("fileInfo", {}).get("path")
            for u in payload.get("upstreams", [])
        ]
        downstreams = [
            d.get("tableInfo", {}).get("name") or d.get("fileInfo", {}).get("path")
            for d in payload.get("downstreams", [])
        ]
        print(f"{table}: HTTP {status} upstreams={upstreams or '[]'} downstreams={downstreams or '[]'}")
    print(f"lineage UI: {dbx.host}/explore/data/{n.catalog}/silver/custbill_history_{n.ns}?activeTab=lineage")
    return 0


def pipeline_name(n: S.Names) -> str:
    return f"ow_tp_custbill_history_dlt_{n.ns}"


def find_pipeline(dbx: Databricks, name: str) -> dict | None:
    payload = dbx.ok("GET", f"/api/2.0/pipelines?filter=name%20LIKE%20'{name}'")
    for entry in payload.get("statuses", []):
        if entry.get("name") == name:
            return entry
    return None


def cmd_pipeline(dbx: Databricks, args) -> int:
    """Declared data-quality rules: the same quarantine policy as the harness,
    expressed as pipeline expectations so violations are reported by Databricks
    rather than by our own SQL."""
    n = names(args)
    source = f"{NOTEBOOK_DIR}/custbill_dlt_{n.ns}"
    dbx.import_notebook(source, S.dlt_source(n), language="SQL")
    spec = {
        "name": pipeline_name(n),
        "catalog": n.catalog,
        "schema": "silver",
        "serverless": True,
        "continuous": False,
        "development": True,
        "libraries": [{"notebook": {"path": source}}],
        "configuration": {"ow_tp.namespace": n.ns},
    }
    existing = find_pipeline(dbx, spec["name"])
    if existing:
        pipeline_id = existing["pipeline_id"]
        dbx.ok("PUT", f"/api/2.0/pipelines/{pipeline_id}", dict(spec, id=pipeline_id))
    else:
        pipeline_id = dbx.ok("POST", "/api/2.0/pipelines", spec)["pipeline_id"]
    print(f"pipeline {pipeline_id} (triggered, no schedule): {dbx.host}/pipelines/{pipeline_id}")
    print(f"  source: {source}")
    return 0


def cmd_run_pipeline(dbx: Databricks, args) -> int:
    n = names(args)
    found = find_pipeline(dbx, pipeline_name(n))
    if not found:
        raise SystemExit(f"pipeline for ns={n.ns} not found; run pipeline first")
    pipeline_id = found["pipeline_id"]
    update = dbx.ok("POST", f"/api/2.0/pipelines/{pipeline_id}/updates", {"full_refresh": args.full_refresh})
    update_id = update["update_id"]
    print(f"pipeline update {update_id}: {dbx.host}/pipelines/{pipeline_id}/updates/{update_id}")
    state = ""
    for _ in range(180):
        detail = dbx.ok("GET", f"/api/2.0/pipelines/{pipeline_id}/updates/{update_id}")["update"]
        state = detail.get("state", "")
        if state in ("COMPLETED", "FAILED", "CANCELED"):
            break
        time.sleep(10)
    print(f"state: {state}")
    if state != "COMPLETED":
        return 1
    for table in (f"custbill_dlt_{n.ns}", f"custbill_dlt_annual_{n.ns}"):
        rows = dbx.sql_ok(f"SELECT count(*) AS rows FROM {n.catalog}.silver.{table}").rows[0][0]
        print(f"  {n.catalog}.silver.{table}: {rows} rows")
    drift = dbx.sql_ok(S.dlt_parity(n))
    print("  parity with harness gold: "
          + ("matches" if not drift.rows else f"DIFFERS on {len(drift.rows)} groups: {drift.rows[:3]}"))
    return 0 if not drift.rows else 1


def cmd_dashboard(dbx: Databricks, args) -> int:
    n = names(args)
    name = f"ow_tp_billing_history_{n.ns}"
    datasets = [
        ("annual", (f"SELECT source_year, currency, record_type, record_count, "
                    f"total_amount_cents / 100.0 AS total_amount FROM {n.gold} ORDER BY source_year")),
        ("quality", f"SELECT reason, count(*) AS records FROM {n.quarantine} GROUP BY reason ORDER BY records DESC"),
        ("recon", (f"SELECT result, count(*) AS checks FROM {n.recon_runs} "
                   f"WHERE run_id = (SELECT run_id FROM {n.recon_runs} ORDER BY checked_at DESC LIMIT 1) "
                   f"GROUP BY result")),
    ]
    spec = {
        "datasets": [
            {"name": key, "displayName": key, "queryLines": [query]} for key, query in datasets
        ],
        "pages": [
            {
                "name": "overview",
                "displayName": "Billing history",
                "layout": [
                    {
                        "widget": {
                            "name": "annual_amount",
                            "queries": [{
                                "name": "annual_amount_q",
                                "query": {
                                    "datasetName": "annual",
                                    "fields": [
                                        {"name": "source_year", "expression": "`source_year`"},
                                        {"name": "sum(total_amount)", "expression": "SUM(`total_amount`)"},
                                    ],
                                    "disaggregated": False,
                                },
                            }],
                            "spec": {
                                "version": 3,
                                "widgetType": "bar",
                                "encodings": {
                                    "x": {"fieldName": "source_year", "scale": {"type": "categorical"}, "displayName": "Year"},
                                    "y": {"fieldName": "sum(total_amount)", "scale": {"type": "quantitative"}, "displayName": "Billed amount"},
                                },
                                "frame": {"title": "Billed amount by year (migrated history)", "showTitle": True},
                            },
                        },
                        "position": {"x": 0, "y": 0, "width": 6, "height": 6},
                    },
                    {
                        "widget": {
                            "name": "quality_table",
                            "queries": [{
                                "name": "quality_q",
                                "query": {
                                    "datasetName": "quality",
                                    "fields": [
                                        {"name": "reason", "expression": "`reason`"},
                                        {"name": "records", "expression": "`records`"},
                                    ],
                                    "disaggregated": True,
                                },
                            }],
                            "spec": {
                                "version": 3,
                                "widgetType": "table",
                                "encodings": {"columns": [
                                    {"fieldName": "reason", "displayName": "Quarantine reason"},
                                    {"fieldName": "records", "displayName": "Records"},
                                ]},
                                "frame": {"title": "Records the legacy parser passed through", "showTitle": True},
                            },
                        },
                        "position": {"x": 6, "y": 0, "width": 6, "height": 6},
                    },
                    {
                        "widget": {
                            "name": "recon_table",
                            "queries": [{
                                "name": "recon_q",
                                "query": {
                                    "datasetName": "recon",
                                    "fields": [
                                        {"name": "result", "expression": "`result`"},
                                        {"name": "checks", "expression": "`checks`"},
                                    ],
                                    "disaggregated": True,
                                },
                            }],
                            "spec": {
                                "version": 3,
                                "widgetType": "table",
                                "encodings": {"columns": [
                                    {"fieldName": "result", "displayName": "Recon result"},
                                    {"fieldName": "checks", "displayName": "Checks"},
                                ]},
                                "frame": {"title": "Latest reconciliation against the legacy baseline", "showTitle": True},
                            },
                        },
                        "position": {"x": 0, "y": 6, "width": 12, "height": 5},
                    },
                ],
            }
        ],
    }
    existing = None
    payload = dbx.ok("GET", "/api/2.0/lakeview/dashboards?page_size=100")
    for dashboard in payload.get("dashboards", []):
        if dashboard.get("display_name") == name and dashboard.get("lifecycle_state") == "ACTIVE":
            existing = dashboard
            break
    body = {
        "display_name": name,
        "warehouse_id": dbx.warehouse_id,
        "serialized_dashboard": json.dumps(spec),
    }
    if existing:
        body["etag"] = existing.get("etag", "")
        result = dbx.ok("PATCH", f"/api/2.0/lakeview/dashboards/{existing['dashboard_id']}", body)
    else:
        result = dbx.ok("POST", "/api/2.0/lakeview/dashboards", body)
    dashboard_id = result["dashboard_id"]
    dbx.call("POST", f"/api/2.0/lakeview/dashboards/{dashboard_id}/published",
             {"embed_credentials": True, "warehouse_id": dbx.warehouse_id})
    print(f"dashboard: {dbx.host}/dashboardsv3/{dashboard_id}/published")
    return 0


def cmd_alert(dbx: Databricks, args) -> int:
    n = names(args)
    name = f"ow_tp_recon_failed_{n.ns}"
    query = (f"WITH checks AS ({S.recon_checks(n)}) "
             "SELECT count_if(result = 'fail') AS failed_checks FROM checks")
    body = {
        "display_name": name,
        "warehouse_id": dbx.warehouse_id,
        "query_text": query,
        "evaluation": {
            "source": {"name": "failed_checks", "aggregation": "FIRST"},
            "comparison_operator": "GREATER_THAN",
            "threshold": {"value": {"double_value": 0}},
        },
        "schedule": {"quartz_cron_schedule": "0 0 6 * * ?", "timezone_id": "UTC", "pause_status": "PAUSED"},
    }
    existing = find_alert(dbx, name)
    if existing:
        dbx.ok("PATCH", f"/api/2.0/alerts/{existing['id']}?update_mask=query_text,evaluation,schedule,warehouse_id", body)
        print(f"alert refreshed (PAUSED): {dbx.host}/sql/alerts/{existing['id']}")
        return 0
    created = dbx.ok("POST", "/api/2.0/alerts", body)
    print(f"alert created (PAUSED): {dbx.host}/sql/alerts/{created['id']}")
    return 0


NOTIFY_NOTEBOOK = '''# Databricks notebook source
# ow_tp recon failure notifier: POSTs to the Devin automation webhook so a
# remediation session starts itself. Runs only when the recon task fails
# (task-level run_if: AT_LEAST_ONE_FAILED).
import json
import urllib.request

dbutils.widgets.text("job_id", "")
dbutils.widgets.text("run_id", "")
dbutils.widgets.text("namespace", "{ns}")
dbutils.widgets.text("base_branch", "{base_branch}")

WEBHOOK_URL = "{webhook}"
SECRET_SCOPE = "{scope}"
SECRET_KEY = "{key}"

job_id = dbutils.widgets.get("job_id")
run_id = dbutils.widgets.get("run_id")
namespace = dbutils.widgets.get("namespace")
base_branch = dbutils.widgets.get("base_branch")
workspace = spark.conf.get("spark.databricks.workspaceUrl")
run_url = f"https://{{workspace}}/jobs/{{job_id}}/runs/{{run_id}}"

payload = {{
    "source": "databricks-recon-job",
    "event": "reconciliation_failed",
    "platform": "databricks",
    "demo": "otterworks-billing-history",
    "namespace": namespace,
    "job_id": job_id,
    "run_id": run_id,
    "run_url": run_url,
    "base_branch": base_branch,
    "catalog": "{catalog}",
    "detail": (
        "OtterWorks CUSTBILL history reconciliation failed on Databricks. "
        "The recon task raise_error message names the failing checks; each check "
        "compares the migrated gold aggregates against the legacy-derived "
        "expectations table."
    ),
}}

request = urllib.request.Request(
    WEBHOOK_URL,
    data=json.dumps(payload).encode(),
    headers={{
        "Content-Type": "application/json",
        "X-Webhook-Secret": dbutils.secrets.get(SECRET_SCOPE, SECRET_KEY),
    }},
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    print("Devin webhook response:", response.status, response.read().decode()[:500])
print("run:", run_url)
'''


def cmd_recon_job(dbx: Databricks, args) -> int:
    n = names(args)
    if not args.webhook_url.startswith("https://"):
        raise SystemExit("--webhook-url must be an https Devin automation webhook URL")
    # these land in notebook source that runs with access to dbutils.secrets, so
    # anything that could close a string literal is rejected
    for label, value in (("--webhook-url", args.webhook_url),
                         ("--secret-scope", args.secret_scope),
                         ("--secret-key", args.secret_key),
                         ("--base-branch", args.base_branch)):
        if not value or not value.isprintable() or any(c in value for c in "\"'\\"):
            raise SystemExit(f"{label} must be printable and free of quotes, backslashes and newlines")
    notebook_path = f"{NOTEBOOK_DIR}/notify_devin_{n.ns}"
    dbx.import_notebook(notebook_path, NOTIFY_NOTEBOOK.format(
        ns=n.ns, webhook=args.webhook_url, scope=args.secret_scope,
        key=args.secret_key, catalog=n.catalog, base_branch=args.base_branch,
    ))
    settings = {
        "name": f"ow_tp_billing_history_recon_{n.ns}",
        "tags": {"project": "otterworks-tp", "demo": "billing-history", "namespace": n.ns},
        "max_concurrent_runs": 1,
        "tasks": [
            {
                "task_key": "recon_check",
                "sql_task": {
                    "warehouse_id": dbx.warehouse_id,
                    "file": {"path": f"{NOTEBOOK_DIR}/recon_check_{n.ns}.sql", "source": "WORKSPACE"},
                },
            },
            {
                "task_key": "notify_devin",
                "depends_on": [{"task_key": "recon_check"}],
                "run_if": "AT_LEAST_ONE_FAILED",
                "notebook_task": {
                    "notebook_path": notebook_path,
                    "base_parameters": {
                        "job_id": "{{job.id}}",
                        "run_id": "{{job.run_id}}",
                        "namespace": n.ns,
                        "base_branch": args.base_branch,
                    },
                },
            },
        ],
        "schedule": {
            "quartz_cron_expression": "0 0 6 * * ?",
            "timezone_id": "UTC",
            "pause_status": "PAUSED",
        },
        "queue": {"enabled": True},
    }
    # the recon SQL lives as a workspace file so the job runs exactly the text
    # the harness runs
    dbx.ok("POST", "/api/2.0/workspace/mkdirs", {"path": NOTEBOOK_DIR})
    import base64
    dbx.ok("POST", "/api/2.0/workspace/import", {
        "path": f"{NOTEBOOK_DIR}/recon_check_{n.ns}.sql",
        "format": "AUTO",
        "overwrite": True,
        "content": base64.b64encode(S.recon_gate(n).encode()).decode(),
    })
    job_id = dbx.upsert_job(settings)
    print(f"recon job {job_id} (schedule PAUSED): {dbx.host}/jobs/{job_id}")
    print(f"  recon SQL:  {NOTEBOOK_DIR}/recon_check_{n.ns}.sql")
    print(f"  notifier:   {notebook_path}")
    return 0


def cmd_run_job(dbx: Databricks, args) -> int:
    n = names(args)
    job = dbx.find_job(f"ow_tp_billing_history_recon_{n.ns}")
    if not job:
        raise SystemExit(f"recon job for ns={n.ns} not found; run recon-job first")
    run_id = dbx.run_job(int(job["job_id"]))
    print(f"triggered run: {dbx.run_url(run_id)}")
    if args.no_wait:
        return 0
    run = dbx.wait_run(run_id)
    state = run.get("state", {})
    print(f"result: {state.get('result_state')} — {str(state.get('state_message'))[:400]}")
    for task in run.get("tasks", []):
        print(f"  task {task['task_key']}: {task.get('state', {}).get('result_state')}")
    return 0 if state.get("result_state") == "SUCCESS" else 1


def cmd_drift(dbx: Databricks, args) -> int:
    """Stage the failure beat: newly arrived history is landed and expected, but
    the migrated target has not been backfilled, so recon goes red."""
    n = names(args)
    data = load_manifest(args)
    if args.kind == "stale":
        new_year = int(data["end_year"]) + 1
        subprocess.run(
            ["perl", str(REPO / "etl/legacy-extra/tools/gen_history_data.pl"),
             n.ns, str(data["start_year"]), str(new_year), str(data["rows_per_month"])],
            cwd=REPO, check=True,
            env=dict(os.environ, OTTERWORKS_LEGACY_ROOT=args.legacy_root),
        )
        cmd_land(dbx, args)
        cmd_expectations(dbx, args)
        print(f"drift staged: {new_year} CUSTBILL history landed and expected; "
              "target not backfilled")
    elif args.kind == "malformed":
        if not (len(args.period) == 6 and args.period.isdigit()):
            raise SystemExit("--kind malformed needs --period YYYYMM")
        target = f"{n.history_dir}/{args.period[:4]}/CUSTBILL_DRIFT_{args.period}.dat"
        rows = [f"HDR CUSTBILL EXTRACT NS={n.ns.upper():<10} PERIOD={args.period}"]
        for index in range(5):
            rows.append(
                f"C99900{index:04d}" + f"{'DRIFT PARTNERS':<30}" + f"{args.period}31"
                + "0000000ABCDE" + "USD" + "01"
            )
        rows.append(f"TRL{5:010d}")
        dbx.put_file(target, ("\n".join(rows) + "\n").encode())
        # landing alone leaves recon green: the bad batch has to reach bronze for
        # the file-count and annual-total checks to diverge from expectations
        dbx.sql_ok(S.delete_bronze_period(n, args.period))
        dbx.sql_ok(S.load_bronze(
            n, f"{n.history_dir}/{args.period[:4]}/CUSTBILL_*_{args.period}.dat", overwrite=False))
        dbx.sql_ok(S.build_silver(n))
        dbx.sql_ok(S.build_quarantine(n))
        dbx.sql_ok(S.build_gold(n))
        print(f"drift staged: malformed batch landed at {target} and ingested for "
              f"period {args.period}")
    return 0


def cmd_status(dbx: Databricks, args) -> int:
    n = names(args)
    result = dbx.sql(
        f"SELECT (SELECT count(*) FROM {n.bronze}) AS bronze_rows, "
        f"(SELECT count(DISTINCT source_file) FROM {n.bronze}) AS files, "
        f"(SELECT min(source_year) FROM {n.bronze}) AS first_year, "
        f"(SELECT max(source_year) FROM {n.bronze}) AS last_year, "
        f"(SELECT count(*) FROM {n.silver}) AS silver_rows, "
        f"(SELECT count(*) FROM {n.quarantine}) AS quarantined, "
        f"(SELECT sum(total_amount_cents) FROM {n.gold}) AS gold_cents, "
        f"(SELECT count(*) FROM {n.expectations}) AS expectation_rows"
    )
    print(json.dumps(result.dicts()[0] if result.ok else {"state": result.state, "error": result.error}, indent=2))
    # the runbook's cost-control step leans on this: nothing should be armed to
    # spin the warehouse up unattended
    job = dbx.find_job(f"ow_tp_billing_history_recon_{n.ns}")
    if job:
        # jobs/list trims settings, so read the schedule from the job itself
        detail = dbx.ok("GET", f"/api/2.1/jobs/get?job_id={int(job['job_id'])}")
        schedule = detail.get("settings", {}).get("schedule")
        state = schedule.get("pause_status", "UNKNOWN") if schedule else "NO SCHEDULE"
        print(f"recon job: {dbx.host}/jobs/{job['job_id']} schedule={state}")
    else:
        print("recon job: absent")
    alert = find_alert(dbx, f"ow_tp_recon_failed_{n.ns}")
    if alert:
        schedule = alert.get("schedule")
        state = schedule.get("pause_status", "UNKNOWN") if schedule else "NO SCHEDULE"
        print(f"recon alert: {dbx.host}/sql/alerts/{alert['id']} schedule={state}")
    else:
        print("recon alert: absent")
    return 0


def cmd_teardown(dbx: Databricks, args) -> int:
    n = names(args)
    for table in (n.gold, n.quarantine, n.silver, n.bronze, n.expectations, n.recon_runs):
        dbx.sql_ok(f"DROP TABLE IF EXISTS {table}")
        print(f"dropped {table}")
    for entry in dbx.list_dir(n.history_dir):
        for inner in dbx.list_dir(entry.get("path", "")):
            dbx.delete_file(inner.get("path", ""))
        # per-year directories outlive their files unless deleted as directories
        dbx.delete_file(entry.get("path", ""))
        dbx.delete_dir(entry.get("path", ""))
    dbx.delete_dir(n.history_dir)
    # n.landing is already namespace-scoped; the volume root itself is shared
    dbx.delete_dir(n.landing)
    job = dbx.find_job(f"ow_tp_billing_history_recon_{n.ns}")
    if job:
        dbx.ok("POST", "/api/2.0/jobs/delete", {"job_id": int(job["job_id"])})
        print(f"deleted job {job['job_id']}")
    pipeline = find_pipeline(dbx, pipeline_name(n))
    if pipeline:
        dbx.call("DELETE", f"/api/2.0/pipelines/{pipeline['pipeline_id']}")
        print(f"deleted pipeline {pipeline['pipeline_id']}")
    # the pipeline's materialized views survive the pipeline, so drop them too
    for view in (f"{n.catalog}.silver.custbill_dlt_annual_{n.ns}",
                 f"{n.catalog}.silver.custbill_dlt_{n.ns}"):
        dbx.sql(f"DROP MATERIALIZED VIEW IF EXISTS {view}")
        dbx.sql(f"DROP TABLE IF EXISTS {view}")
        print(f"dropped {view}")
    dashboards = dbx.ok("GET", "/api/2.0/lakeview/dashboards?page_size=100")
    for dashboard in dashboards.get("dashboards", []):
        if dashboard.get("display_name") == f"ow_tp_billing_history_{n.ns}":
            dbx.call("DELETE", f"/api/2.0/lakeview/dashboards/{dashboard['dashboard_id']}")
            print(f"trashed dashboard {dashboard['dashboard_id']}")
    alert = find_alert(dbx, f"ow_tp_recon_failed_{n.ns}")
    if alert:
        dbx.call("DELETE", f"/api/2.0/alerts/{alert['id']}")
        print(f"deleted alert {alert['id']}")
    for path in (f"{NOTEBOOK_DIR}/notify_devin_{n.ns}",
                 f"{NOTEBOOK_DIR}/recon_check_{n.ns}.sql",
                 f"{NOTEBOOK_DIR}/custbill_dlt_{n.ns}"):
        status, _ = dbx.call("POST", "/api/2.0/workspace/delete", {"path": path})
        print(f"workspace delete {path}: HTTP {status}")
    # negative verification across every object class teardown touches, so a
    # survivor like the alert cannot hide behind a table-only scan
    remaining = dbx.sql(f"SHOW TABLES IN {n.catalog}.silver LIKE '*_{n.ns}'")
    leftovers = {
        "silver_tables": remaining.rows,
        "recon_job": dbx.find_job(f"ow_tp_billing_history_recon_{n.ns}") is not None,
        "pipeline": find_pipeline(dbx, pipeline_name(n)) is not None,
        "alert": find_alert(dbx, f"ow_tp_recon_failed_{n.ns}") is not None,
        "dashboard": any(
            d.get("display_name") == f"ow_tp_billing_history_{n.ns}"
            and d.get("lifecycle_state") == "ACTIVE"
            for d in dbx.ok("GET", "/api/2.0/lakeview/dashboards?page_size=100").get("dashboards", [])
        ),
        "landed_paths": [e.get("path") for e in dbx.list_dir(n.history_dir)],
    }
    print("negative verification: " + json.dumps(leftovers))
    survivors = [k for k, v in leftovers.items() if v]
    if survivors:
        print(f"teardown incomplete, survivors: {survivors}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--catalog", default="ow_tp")
    parser.add_argument("--legacy-root", default="/tmp/otterworks-legacy")
    parser.add_argument("--expectations-file", default="")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("provision")
    land = sub.add_parser("land")
    land.add_argument("--period", default="")
    sub.add_parser("expectations")
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--period", default="")
    recon = sub.add_parser("recon")
    recon.add_argument("--out", default="")
    timetravel = sub.add_parser("timetravel")
    timetravel.add_argument("--table", default="gold", choices=["bronze", "silver", "gold"])
    timetravel.add_argument("--limit", type=int, default=5)
    sub.add_parser("lineage")
    sub.add_parser("dashboard")
    sub.add_parser("alert")
    sub.add_parser("pipeline")
    run_pipeline = sub.add_parser("run-pipeline")
    run_pipeline.add_argument("--full-refresh", action="store_true")
    job = sub.add_parser("recon-job")
    job.add_argument("--webhook-url", required=True)
    job.add_argument("--base-branch", default="tech-partnerships",
                     help="branch the remediation session should base its audit PR on")
    job.add_argument("--secret-scope", default="ow_tp")
    job.add_argument("--secret-key", default="devin_webhook_secret")
    run = sub.add_parser("run-job")
    run.add_argument("--no-wait", action="store_true")
    drift = sub.add_parser("drift")
    drift.add_argument("--kind", default="stale", choices=["stale", "malformed"])
    drift.add_argument("--period", default="")
    sub.add_parser("status")
    sub.add_parser("teardown")

    args = parser.parse_args()
    if args.command == "drift" and args.kind == "malformed" and not (len(args.period) == 6 and args.period.isdigit()):
        raise SystemExit("--period YYYYMM is required for --kind malformed")
    if args.command == "drift":
        args.period = args.period if args.kind == "malformed" else ""
    commands = {
        "provision": cmd_provision, "land": cmd_land, "expectations": cmd_expectations,
        "backfill": cmd_backfill, "recon": cmd_recon, "timetravel": cmd_timetravel,
        "lineage": cmd_lineage, "dashboard": cmd_dashboard, "alert": cmd_alert,
        "pipeline": cmd_pipeline, "run-pipeline": cmd_run_pipeline,
        "recon-job": cmd_recon_job, "run-job": cmd_run_job, "drift": cmd_drift,
        "status": cmd_status, "teardown": cmd_teardown,
    }
    try:
        return commands[args.command](Databricks(), args)
    except DbxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

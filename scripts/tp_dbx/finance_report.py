#!/usr/bin/env python3
"""Converted finance report unit: finance_excel_report.pl -> ow_tp_finance_<ns>.

Replaces the 2004 Perl "CSV renamed to .xls" report with a gold Delta aggregate
under Unity Catalog plus a verifiable delivery audit. Everything is namespace
scoped (--ns) and suffixed: this is a shared workspace, so the tool only ever
creates/updates `ow_tp.*.*_<ns>` tables, `/Volumes/ow_tp/bronze/landing/<ns>/
finance_report/...` files and the `ow_tp_finance_<ns>` job. It never drops or
replaces a shared table and never creates clusters (serverless only).

  provision   create the unit's suffixed silver/gold tables (IF NOT EXISTS)
  land        upload local parsed CUSTBILL .psv files into the landing volume
  deploy-job  create/update the (paused, manual-trigger) workspace job
  run-job     trigger the job and wait; params: report date + input subdir
  recon       recompute from gold/silver, compare to the golden legacy CSV,
              prove idempotency by an actual rerun, emit a schema-valid report
  fixture-recon  offline recompute over fixture-landed .psv files (run_mode:
              fixture; transport-and-aggregation parity only, no SQL/Delta)
  clean       remove this namespace's landed files (tables are kept)

Legacy behaviour reproduced (see etl/legacy-extra/jobs/finance_excel_report.pl):
group parsed records by currency + record type, count and 2-decimal total,
rows with an empty customer id are skipped, record types map 01->INVOICE,
02->CREDIT, anything else -> UNKNOWN(rt), output ordered by the string sort of
"<ccy>|<rt>". Deficiencies retired: the artifact is a real CSV with a .csv
extension (never a lying .xls), delivery is verified against the volume and
recorded in gold.finance_report_delivery_<ns> instead of a silent sendmail
no-op, and reruns replace the (ns, report_date) slice instead of duplicating.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import urllib.parse
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import Databricks, DbxError, require_ident, require_ns  # noqa: E402

CATALOG = "ow_tp"
NOTEBOOK_DIR = "/Shared/ow_tp"


def names(ns: str) -> dict:
    require_ns(ns)
    return {
        "ns": ns,
        "silver": f"{CATALOG}.silver.custbill_records_{ns}",
        "summary": f"{CATALOG}.gold.finance_billing_summary_{ns}",
        "delivery": f"{CATALOG}.gold.finance_report_delivery_{ns}",
        "landing": f"/Volumes/{CATALOG}/bronze/landing/{ns}/finance_report",
        "job": f"ow_tp_finance_{ns}",
        "notebook": f"{NOTEBOOK_DIR}/finance_report_{ns}",
    }


PROVISION = [
    """CREATE TABLE IF NOT EXISTS {silver} (
        ns STRING NOT NULL,
        source_file STRING,
        cust_id STRING NOT NULL,
        cust_name STRING,
        bill_date DATE,
        amount DECIMAL(18,2),
        currency STRING,
        record_type_code STRING,
        report_date DATE NOT NULL
    ) COMMENT 'Parsed CUSTBILL records for the finance-report unit (ns-suffixed slice)'""",
    """CREATE TABLE IF NOT EXISTS {summary} (
        ns STRING NOT NULL,
        currency STRING NOT NULL,
        record_type_code STRING NOT NULL,
        record_type STRING NOT NULL,
        record_count BIGINT NOT NULL,
        total_amount DECIMAL(18,2) NOT NULL,
        report_date DATE NOT NULL,
        generated_at TIMESTAMP NOT NULL
    ) COMMENT 'Finance billing summary by currency and record type (system of record for the legacy .xls)'""",
    """CREATE TABLE IF NOT EXISTS {delivery} (
        ns STRING NOT NULL,
        report_date DATE NOT NULL,
        artifact_path STRING NOT NULL,
        artifact_sha256 STRING NOT NULL,
        recipient_list STRING NOT NULL,
        delivery_status STRING NOT NULL,
        rows_loaded BIGINT NOT NULL,
        rows_skipped_empty_cust BIGINT NOT NULL,
        rows_attributed_malformed BIGINT NOT NULL,
        delivered_at TIMESTAMP NOT NULL
    ) COMMENT 'Verified delivery audit the legacy sendmail no-op never produced'""",
]

# The converted job body. Deployed as a workspace notebook and run by the
# ow_tp_finance_<ns> job (serverless notebook task); the harness runs exactly
# this text, parameterised only by widgets.
NOTEBOOK = r'''# Databricks notebook source
# Converted finance report (legacy: etl/legacy-extra/jobs/finance_excel_report.pl).
# Aggregates parsed CUSTBILL records by currency + record type into a gold Delta
# table, writes a real CSV artifact to the landing volume, verifies it, and
# records the delivery. Idempotent per (ns, report_date).
import hashlib
import re

dbutils.widgets.text("ns", "__NS__")
dbutils.widgets.text("report_date", "2026-01-15")
dbutils.widgets.text("input_subdir", "parsed")

ns = dbutils.widgets.get("ns")
report_date = dbutils.widgets.get("report_date")
input_subdir = dbutils.widgets.get("input_subdir")
if not re.fullmatch(r"[a-z0-9_]{1,24}", ns):
    raise ValueError(f"bad ns: {ns!r}")
if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
    raise ValueError(f"bad report_date: {report_date!r}")
if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", input_subdir):
    raise ValueError(f"bad input_subdir: {input_subdir!r}")

catalog = "ow_tp"
silver = f"{catalog}.silver.custbill_records_{ns}"
summary = f"{catalog}.gold.finance_billing_summary_{ns}"
delivery = f"{catalog}.gold.finance_report_delivery_{ns}"
landing = f"/Volumes/{catalog}/bronze/landing/{ns}/finance_report"
parsed_dir = f"{landing}/{input_subdir}"

# COMMAND ----------
# Silver load: replace this namespace's slice from the landed .psv files.
# The legal empty-input case is an input directory that EXISTS and contains no
# CUSTBILL files (`land --allow-empty` creates the empty directory). A missing
# or unlistable directory aborts before the destructive slice DELETE below,
# exactly like the legacy `opendir(...) || die`.
# Legacy input selection: grep { /^CUSTBILL.*\.psv$/ } readdir(D)
psv_files = [f for f in dbutils.fs.ls(parsed_dir)
             if f.name.startswith("CUSTBILL") and f.name.endswith(".psv")]

spark.sql(f"DELETE FROM {silver} WHERE ns = '{ns}' "
          f"AND (report_date = DATE'{report_date}' OR report_date IS NULL)")
rows_loaded = rows_skipped = rows_attributed = 0
if psv_files:
    schema = ("cust_id STRING, cust_name STRING, bill_date STRING, "
              "amount STRING, currency STRING, record_type STRING")
    # Literal pipe split like the legacy split(/\|/): no quote/escape semantics.
    raw = (spark.read.format("csv")
           .option("sep", "|").option("header", "false")
           .option("quote", "\u0000").option("escape", "\u0000")
           .option("mode", "PERMISSIVE")
           .schema(schema)
           .load(f"{parsed_dir}/CUSTBILL*.psv"))
    raw = raw.selectExpr("*", "_metadata.file_name AS source_file")
    raw.createOrReplaceTempView(f"finance_raw_{ns}")
    # Legacy parity: skip rows with an empty customer id (perl: next if $cust eq "").
    # Malformed rows must not fail open into a plausible row: rows with an
    # uncastable amount or a missing/empty currency or record type (truncated
    # lines) are counted and excluded, never coerced.
    rows_skipped = spark.sql(
        f"SELECT count(*) FROM finance_raw_{ns} WHERE cust_id IS NULL OR cust_id = ''"
    ).collect()[0][0]
    valid_pred = ("cust_id IS NOT NULL AND cust_id <> '' "
                  "AND try_cast(amount AS DECIMAL(18,2)) IS NOT NULL "
                  "AND currency IS NOT NULL AND currency <> '' "
                  "AND record_type IS NOT NULL AND record_type <> ''")
    rows_attributed = spark.sql(
        f"""SELECT count(*) FROM finance_raw_{ns}
            WHERE cust_id IS NOT NULL AND cust_id <> ''
              AND NOT ({valid_pred})"""
    ).collect()[0][0]
    spark.sql(f"""
        INSERT INTO {silver}
        SELECT '{ns}', source_file, cust_id, cust_name,
               try_cast(bill_date AS DATE),
               try_cast(amount AS DECIMAL(18,2)),
               currency, record_type, DATE'{report_date}'
        FROM finance_raw_{ns}
        WHERE {valid_pred}
    """)
    rows_loaded = spark.sql(
        f"SELECT count(*) FROM {silver} WHERE ns = '{ns}' AND report_date = DATE'{report_date}'"
    ).collect()[0][0]

# COMMAND ----------
# Gold aggregate: the legacy report, expressed in SQL. Replace, never append.
spark.sql(f"DELETE FROM {summary} WHERE ns = '{ns}' AND report_date = DATE'{report_date}'")
spark.sql(f"""
    INSERT INTO {summary}
    SELECT '{ns}', currency, record_type_code,
           CASE record_type_code WHEN '01' THEN 'INVOICE'
                                 WHEN '02' THEN 'CREDIT'
                                 ELSE concat('UNKNOWN(', record_type_code, ')') END,
           count(*), CAST(sum(amount) AS DECIMAL(18,2)),
           DATE'{report_date}', current_timestamp()
    FROM {silver}
    WHERE ns = '{ns}' AND report_date = DATE'{report_date}'
    GROUP BY currency, record_type_code
""")

# COMMAND ----------
# Artifact: a real CSV (extension tells the truth), ordered like the legacy
# report (string sort of "<ccy>|<rt>").
rows = spark.sql(f"""
    SELECT currency, record_type, record_count, total_amount
    FROM {summary}
    WHERE ns = '{ns}' AND report_date = DATE'{report_date}'
    ORDER BY currency, record_type_code
""").collect()
lines = ["Currency,RecordType,RecordCount,TotalAmount"]
for r in rows:
    lines.append(f"{r.currency},{r.record_type},{r.record_count},{r.total_amount:.2f}")
csv_text = "\n".join(lines) + "\n"
stamp = report_date.replace("-", "")
artifact_path = f"{landing}/reports/finance_billing_{stamp}.csv"
dbutils.fs.put(artifact_path, csv_text, True)

# Verified delivery: read the artifact back and compare digests.
written = dbutils.fs.head(artifact_path, 1024 * 1024)
digest = hashlib.sha256(csv_text.encode()).hexdigest()
status = ("VOLUME_VERIFIED; MAIL=NO_TRANSPORT_CONFIGURED"
          if hashlib.sha256(written.encode()).hexdigest() == digest
          else "VERIFICATION_FAILED")
# Record only whether a managed distribution list exists — never its value.
# The secret must not reach SQL text, the audit table, or query history.
try:
    dbutils.secrets.get("ow_tp", "finance_distribution_list")
    recipients = "configured (managed list in secret scope ow_tp; value not recorded)"
except Exception:
    recipients = "unconfigured (managed list absent from secret scope ow_tp)"

spark.sql(f"DELETE FROM {delivery} WHERE ns = '{ns}' AND report_date = DATE'{report_date}'")
spark.sql(f"""
    INSERT INTO {delivery} VALUES (
        '{ns}', DATE'{report_date}', '{artifact_path}', '{digest}',
        '{recipients}', '{status}',
        {rows_loaded}, {rows_skipped}, {rows_attributed}, current_timestamp()
    )
""")
if status != "VOLUME_VERIFIED; MAIL=NO_TRANSPORT_CONFIGURED":
    raise RuntimeError(f"artifact verification failed for {artifact_path}")
print(f"finance report done: {len(rows)} summary rows, artifact {artifact_path}")
'''


def cmd_provision(dbx: Databricks, args) -> int:
    n = names(args.ns)
    for stmt in PROVISION:
        dbx.sql_ok(stmt.format(**n))
    # Evolve this unit's own silver slice in place if it predates report_date
    # scoping (additive column only; never a drop/replace).
    try:
        dbx.sql_ok(f"ALTER TABLE {n['silver']} ADD COLUMNS (report_date DATE)")
    except DbxError as e:
        if "already exists" not in str(e).lower() and "FIELDS_ALREADY_EXISTS" not in str(e):
            raise
    print(f"provisioned {n['silver']}, {n['summary']}, {n['delivery']}")
    return 0


def cmd_land(dbx: Databricks, args) -> int:
    n = names(args.ns)
    require_ident(args.subdir, "subdir")
    source = Path(args.source)
    files = sorted(source.glob("*.psv"))
    if not files and not args.allow_empty:
        raise SystemExit(f"no .psv files under {source} (use --allow-empty for the empty-input case)")
    for f in files:
        target = f"{n['landing']}/{args.subdir}/{f.name}"
        dbx.put_file(target, f.read_bytes())
        print(f"landed {target} ({f.stat().st_size} bytes)")
    if not files:
        # Materialise the empty directory so the job can tell an intended empty
        # batch (directory exists, no files) from a misconfigured path (abort).
        quoted = urllib.parse.quote(f"{n['landing']}/{args.subdir}", safe="/")
        dbx.ok("PUT", f"/api/2.0/fs/directories{quoted}")
        print(f"landed nothing (empty-input case) under {n['landing']}/{args.subdir}/")
    return 0


def cmd_deploy_job(dbx: Databricks, args) -> int:
    n = names(args.ns)
    dbx.import_notebook(n["notebook"], NOTEBOOK.replace("__NS__", n["ns"]))
    settings = {
        "name": n["job"],
        "tags": {"project": "otterworks-tp", "demo": "finance-report", "namespace": n["ns"]},
        "max_concurrent_runs": 1,
        "tasks": [{
            "task_key": "finance_report",
            "notebook_task": {
                "notebook_path": n["notebook"],
                "base_parameters": {
                    "ns": n["ns"],
                    "report_date": "{{job.parameters.report_date}}",
                    "input_subdir": "{{job.parameters.input_subdir}}",
                },
            },
        }],
        "parameters": [
            {"name": "report_date", "default": "2026-01-15"},
            {"name": "input_subdir", "default": "parsed"},
        ],
        "schedule": {
            # the legacy cron was daily 02:10 overlapping analytics_daily; the
            # converted job is manual-trigger only for the demo (PAUSED).
            "quartz_cron_expression": "0 10 2 * * ?",
            "timezone_id": "UTC",
            "pause_status": "PAUSED",
        },
        "queue": {"enabled": True},
    }
    job_id = dbx.upsert_job(settings)
    print(f"job {n['job']} = {job_id} (schedule PAUSED): {dbx.host}/jobs/{job_id}")
    return 0


def cmd_run_job(dbx: Databricks, args) -> int:
    n = names(args.ns)
    require_date(args.report_date, "report-date")
    require_ident(args.subdir, "subdir")
    job = dbx.find_job(n["job"])
    if not job:
        raise SystemExit(f"job {n['job']} not found; run deploy-job first")
    run_id = dbx.run_job(int(job["job_id"]), {
        "report_date": args.report_date, "input_subdir": args.subdir,
    })
    run = dbx.wait_run(run_id)
    result = run.get("state", {}).get("result_state", "")
    print(f"run {run_id}: {result} {dbx.run_url(run_id)}")
    return 0 if result == "SUCCESS" else 1


def parse_golden(path: Path) -> dict[str, tuple[int, str]]:
    lines = path.read_text().splitlines()
    if lines[0] != "Currency,RecordType,RecordCount,TotalAmount":
        raise SystemExit(f"{path}: unexpected golden header {lines[0]!r}")
    out = {}
    for line in lines[1:]:
        ccy, rt, cnt, tot = line.split(",")
        out[f"{ccy}/{rt}"] = (int(cnt), tot)
    return out


def require_date(value: str, label: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SystemExit(f"{label} must be YYYY-MM-DD: {value!r}")
    return value


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_volume_bytes(dbx: Databricks, volume_path: str) -> bytes:
    import urllib.request
    quoted = urllib.parse.quote(volume_path, safe="/")
    req = urllib.request.Request(
        dbx.host + f"/api/2.0/fs/files{quoted}",
        headers={"Authorization": f"Bearer {dbx.token}"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read()


def summary_grid(dbx: Databricks, n: dict, report_date: str) -> list[list]:
    return dbx.sql_ok(
        f"SELECT currency, record_type, record_count, CAST(total_amount AS STRING) "
        f"FROM {n['summary']} WHERE ns = '{n['ns']}' AND report_date = DATE'{report_date}' "
        f"ORDER BY currency, record_type_code").rows


def cmd_recon(dbx: Databricks, args) -> int:
    n = names(args.ns)
    require_date(args.report_date, "report-date")
    if bool(args.empty_report_date) != bool(args.empty_golden):
        raise SystemExit("--empty-report-date and --empty-golden must be given together "
                         "(or both omitted, which is recorded as unverified)")
    if args.empty_report_date:
        require_date(args.empty_report_date, "empty-report-date")
    require_ident(args.subdir, "subdir")
    golden = parse_golden(Path(args.golden))
    golden_bytes = Path(args.golden).read_bytes()
    empty_golden_bytes = Path(args.empty_golden).read_bytes() if args.empty_golden else None
    checks = []

    def check(cid, expected, actual, source):
        checks.append({"id": cid, "expected": expected, "actual": actual,
                       "source_of_truth": source,
                       "result": "pass" if expected == actual else "fail"})

    grid = summary_grid(dbx, n, args.report_date)
    actual_map = {f"{r[0]}/{r[1]}": (int(r[2]), f"{Decimal(r[3]):.2f}") for r in grid}
    for key in sorted(set(golden) | set(actual_map)):
        exp = golden.get(key)
        act = actual_map.get(key)
        check(f"summary/{key}",
              f"{exp[0]}|{exp[1]}" if exp else "absent",
              f"{act[0]}|{act[1]}" if act else "absent",
              f"golden legacy CSV {args.golden} vs {n['summary']} (recomputed via SQL)")

    # Cross-foot: gold must equal an aggregation recomputed directly from silver.
    crossfoot = dbx.sql_ok(f"""
        WITH s AS (SELECT currency, record_type_code, count(*) c,
                          CAST(sum(amount) AS DECIMAL(18,2)) t
                   FROM {n['silver']}
                   WHERE ns = '{n['ns']}' AND report_date = DATE'{args.report_date}'
                   GROUP BY currency, record_type_code),
             g AS (SELECT currency, record_type_code, record_count, total_amount
                   FROM {n['summary']}
                   WHERE ns = '{n['ns']}' AND report_date = DATE'{args.report_date}')
        SELECT count(*) FROM g
        FULL OUTER JOIN s ON g.currency = s.currency AND g.record_type_code = s.record_type_code
        WHERE g.record_count IS DISTINCT FROM s.c OR g.total_amount IS DISTINCT FROM s.t
    """).scalar()
    check("crossfoot/gold-vs-silver", 0, int(crossfoot), f"{n['summary']} vs re-aggregated {n['silver']}")
    total_count = dbx.sql_ok(
        f"SELECT COALESCE(sum(record_count),0) FROM {n['summary']} "
        f"WHERE ns='{n['ns']}' AND report_date=DATE'{args.report_date}'").scalar()
    check("crossfoot/total-record-count", sum(v[0] for v in golden.values()), int(total_count),
          f"golden legacy CSV vs sum(record_count) in {n['summary']}")

    # Delivery audit row: explicit, truthful status.
    drow = dbx.sql_ok(
        f"SELECT delivery_status, artifact_path, artifact_sha256 FROM {n['delivery']} "
        f"WHERE ns='{n['ns']}' AND report_date=DATE'{args.report_date}'").rows
    check("delivery/audit-row-count", 1, len(drow), n["delivery"])
    status_val = drow[0][0] if drow else "absent"
    check("delivery/status-explicit-no-transport",
          "VOLUME_VERIFIED; MAIL=NO_TRANSPORT_CONFIGURED", status_val, n["delivery"])

    # Artifact: valid CSV, .csv extension, byte-identical to the golden report.
    artifact_ok = False
    if drow:
        artifact_path = drow[0][1]
        payload = get_volume_bytes(dbx, artifact_path)
        check("artifact/extension-truthful", ".csv", artifact_path[artifact_path.rfind("."):],
              "converted job must not reproduce the CSV-named-.xls defect")
        check("artifact/sha256-matches-audit", drow[0][2], hashlib.sha256(payload).hexdigest(),
              f"{artifact_path} vs {n['delivery']}.artifact_sha256")
        check("artifact/bytes-equal-golden", golden_bytes.decode(), payload.decode(),
              f"{args.golden} vs {artifact_path}")
        artifact_ok = True

    # Empty-input case: header-only artifact and zero summary rows.
    empty_input_verified = args.empty_report_date and empty_golden_bytes is not None
    if empty_input_verified:
        empty_grid = summary_grid(dbx, n, args.empty_report_date)
        check("empty-input/zero-summary-rows", 0, len(empty_grid), n["summary"])
        stamp = args.empty_report_date.replace("-", "")
        empty_path = f"{n['landing']}/reports/finance_billing_{stamp}.csv"
        payload = get_volume_bytes(dbx, empty_path)
        check("empty-input/header-only-artifact", empty_golden_bytes.decode(), payload.decode(),
              f"{args.empty_golden} vs {empty_path}")

    # Idempotency: actually rerun the job, then compare the summary grid.
    before = grid
    job = dbx.find_job(n["job"])
    if not job:
        # The recon schema pins idempotency_rerun.performed to true, so there is
        # no honest way to emit a report without an actual rerun: fail loudly.
        raise SystemExit(f"job {n['job']} not found; cannot perform idempotency rerun")
    run_id = dbx.run_job(int(job["job_id"]),
                         {"report_date": args.report_date, "input_subdir": args.subdir})
    run = dbx.wait_run(run_id)
    rerun_ok = run.get("state", {}).get("result_state") == "SUCCESS"
    after = summary_grid(dbx, n, args.report_date)
    check("idempotency/rerun-grid-identical", before, after,
          f"jobs run {run_id} rerun then re-read {n['summary']}")
    dup = dbx.sql_ok(
        f"SELECT count(*) FROM {n['summary']} WHERE ns='{n['ns']}' "
        f"AND report_date=DATE'{args.report_date}'").scalar()
    check("idempotency/no-duplicate-rows", len(golden), int(dup), n["summary"])

    report = {
        "kind": "recon-report",
        "unit": "finance_excel_report",
        "namespace": n["ns"],
        "generated_at": utcnow(),
        "run_mode": "live",
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {"performed": True, "result": "pass" if rerun_ok else "fail",
                              "evidence": "jobs run-now rerun of ow_tp_finance_%s, summary grid compared before/after" % n["ns"]},
        "planted_anomaly_detections": {"expected_set": [], "actual_set": [], "missing": [], "unexpected": []},
        "unverified_paths": [
            "mail transport: no SMTP exists in the demo workspace; delivery is volume-verified and the non-delivery is recorded explicitly",
            "UNKNOWN(record_type) mapping: generator emits only 01/02 for this namespace (declared coverage_gap in the contract)",
            "empty-customer-id skip and malformed-amount attribution: generator plants no such rows (declared coverage_gap in the contract)",
        ] + ([] if artifact_ok else ["artifact comparison skipped: no delivery row"])
          + ([] if empty_input_verified else
             ["empty-input case not exercised: --empty-report-date/--empty-golden not supplied"]),
    }
    out = Path(args.out)
    out.write_text(json.dumps(report, indent=2) + "\n")
    failed = [c["id"] for c in checks if c["result"] != "pass"]
    if not rerun_ok:
        failed.append("idempotency/rerun-succeeded")
    print(f"recon: {len(checks)} checks, {len(failed)} failed -> {out}")
    for cid in failed:
        print(f"  FAIL {cid}")
    return 1 if failed else 0


def legacy_aggregate(psv_dir: Path) -> dict[str, tuple[int, str]]:
    """Reference implementation of the legacy aggregation (Decimal, not float)."""
    tot: dict[str, Decimal] = {}
    cnt: dict[str, int] = {}
    for f in sorted(psv_dir.glob("CUSTBILL*.psv")):
        for line in f.read_text().splitlines():
            fields = line.split("|")
            if not fields or fields[0] == "":
                continue
            _, _, _, amt, ccy, rt = (fields + [""] * 6)[:6]
            # Mirror the notebook's malformed-row policy: rows with an
            # uncastable amount or a missing currency/record type are
            # attributed and excluded, never coerced or crashed on.
            try:
                amount = Decimal(amt)
            except InvalidOperation:
                continue
            if ccy == "" or rt == "":
                continue
            key = f"{ccy}|{rt}"
            tot[key] = tot.get(key, Decimal(0)) + amount
            cnt[key] = cnt.get(key, 0) + 1
    out = {}
    for key in sorted(tot):
        ccy, rt = key.split("|")
        rtname = "INVOICE" if rt == "01" else "CREDIT" if rt == "02" else f"UNKNOWN({rt})"
        out[f"{ccy}/{rtname}"] = (cnt[key], f"{tot[key]:.2f}")
    return out


def cmd_fixture_recon(args) -> int:
    golden = parse_golden(Path(args.golden))
    actual = legacy_aggregate(Path(args.parsed_dir))
    checks = []
    for key in sorted(set(golden) | set(actual)):
        exp, act = golden.get(key), actual.get(key)
        checks.append({
            "id": f"summary/{key}",
            "expected": f"{exp[0]}|{exp[1]}" if exp else "absent",
            "actual": f"{act[0]}|{act[1]}" if act else "absent",
            "source_of_truth": f"golden legacy CSV {args.golden} vs Decimal recompute over {args.parsed_dir}",
            "result": "pass" if exp == act else "fail",
        })
    rerun = legacy_aggregate(Path(args.parsed_dir))
    checks.append({
        "id": "idempotency/recompute-stable",
        "expected": sorted(actual.items()),
        "actual": sorted(rerun.items()),
        "source_of_truth": "second recompute over the same fixture-landed files",
        "result": "pass" if actual == rerun else "fail",
    })
    report = {
        "kind": "recon-report",
        "unit": "finance_excel_report",
        "namespace": args.ns,
        "generated_at": utcnow(),
        "run_mode": "fixture",
        "checks": checks,
        "values_recomputed_from_target": False,
        "idempotency_rerun": {"performed": True,
                              "result": "pass" if actual == rerun else "fail",
                              "evidence": "aggregation recomputed twice over fixture-landed files"},
        "planted_anomaly_detections": {"expected_set": [], "actual_set": [], "missing": [], "unexpected": []},
        "unverified_paths": [
            "fixture mode: SQL execution, Delta semantics, Unity Catalog, warehouse behaviour and volume transport are live checks (see the live recon report)",
        ],
    }
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    failed = [c["id"] for c in checks if c["result"] != "pass"]
    print(f"fixture recon: {len(checks)} checks, {len(failed)} failed -> {args.out}")
    return 1 if failed else 0


def cmd_clean(dbx: Databricks, args) -> int:
    n = names(args.ns)
    for sub in ("parsed", "parsed_empty", "reports"):
        base = f"{n['landing']}/{sub}"
        for entry in dbx.list_dir(base):
            if not entry.get("is_directory"):
                dbx.delete_file(entry["path"])
                print(f"deleted {entry['path']}")
        dbx.delete_dir(base)
    dbx.delete_dir(n["landing"])
    print(f"cleaned {n['landing']} (tables kept; recon evidence lives in the repo)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ns", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("provision")
    land = sub.add_parser("land")
    land.add_argument("--source", required=True)
    land.add_argument("--subdir", default="parsed")
    land.add_argument("--allow-empty", action="store_true")
    sub.add_parser("deploy-job")
    run = sub.add_parser("run-job")
    run.add_argument("--report-date", required=True)
    run.add_argument("--subdir", default="parsed")
    recon = sub.add_parser("recon")
    recon.add_argument("--golden", required=True)
    recon.add_argument("--report-date", required=True)
    recon.add_argument("--subdir", default="parsed")
    recon.add_argument("--empty-golden")
    recon.add_argument("--empty-report-date")
    recon.add_argument("--out", required=True)
    fx = sub.add_parser("fixture-recon")
    fx.add_argument("--golden", required=True)
    fx.add_argument("--parsed-dir", required=True)
    fx.add_argument("--out", required=True)
    sub.add_parser("clean")
    args = p.parse_args()
    if args.cmd == "fixture-recon":
        return cmd_fixture_recon(args)
    dbx = Databricks()
    return {
        "provision": cmd_provision,
        "land": cmd_land,
        "deploy-job": cmd_deploy_job,
        "run-job": cmd_run_job,
        "recon": cmd_recon,
        "clean": cmd_clean,
    }[args.cmd](dbx, args)


if __name__ == "__main__":
    raise SystemExit(main())

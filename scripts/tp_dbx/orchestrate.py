#!/usr/bin/env python3
"""Converted orchestration unit: crontab + run_all.sh -> ow_tp_orchestrate_<ns>.

The legacy chain was coupled by cron time offsets (*/15 ingest, 5-59/15 parse,
02:10 finance, Sunday 06:00 run_all) and run_all.sh sequenced the three jobs
with `|| true` and fixed sleeps: a failed stage was silently swallowed and the
next stage ran anyway over whatever state was left behind. The converted unit
is ONE dependency-driven Databricks Workflow whose tasks are the three
converted units' logic parameterised to this namespace slice:

    ingest -> parse -> publish_psv -> finance      (each with depends_on)

publish_psv is the explicit replacement for the filesystem handoff the legacy
chain relied on (parse wrote parsed/*.psv which finance read 5 cron-minutes
later): it renders the parse unit's silver rows back into the exact legacy
.psv record bytes and publishes them to the finance unit's input directory.

Failure semantics retired: no `|| true`, no sleeps, no "hope 5 minutes is
enough". A failed upstream task fails the run and blocks every downstream
task (proven live by a chaos-injected parse failure); tasks never retry
silently (max_retries=0) and reruns are queued, never concurrent
(max_concurrent_runs=1). The weekly Sunday 06:00 cadence is modelled as a
PAUSED schedule; the demo trigger path is manual run-now.

  baseline   derive the golden baseline from the deterministic legacy
             run_all.sh run (local, no Databricks)
  provision  create this namespace's tables (IF NOT EXISTS; shared infra is
             parent-owned and never touched)
  deploy     import the four notebooks and create/update the workflow
  land       upload the legacy drop bytes into this namespace's ingest drop
  run        trigger the workflow and wait; prints per-task result states
  recon      recompute every check from the target platform, prove the
             chaos-blocking semantics and an idempotent rerun, emit a
             schema-valid recon report
  clean      remove this namespace's landed volume files (tables and the
             committed evidence are kept)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import finance_report as F  # noqa: E402
import parse_sql as S  # noqa: E402
from client import Databricks, require_ns  # noqa: E402
from ingest import split_records  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
NOTEBOOK_DIR = "/Shared/ow_tp"
CATALOG = "ow_tp"
UNIT = "run_all_orchestration"


class OrchParseNames(S.Names):
    """The parse unit's SQL, retargeted at this unit's chain handoff: bronze
    loads from the ingest task's incoming/ directory (same CUSTBILL*.dat match
    set as the legacy glob) instead of a directly-landed parse drop."""

    @property
    def drop_dir(self) -> str:
        return f"{self.landing}/sftp_ingest_poll/incoming/CUSTBILL*.dat"


def names(ns: str) -> dict:
    require_ns(ns)
    p = OrchParseNames(catalog=CATALOG, ns=ns)
    f = F.names(ns)
    return {
        "ns": ns,
        "parse": p,
        "finance": f,
        "job": f"ow_tp_orchestrate_{ns}",
        "ingest_notebook": f"{NOTEBOOK_DIR}/orchestrate_ingest_{ns}",
        "parse_notebook": f"{NOTEBOOK_DIR}/orchestrate_parse_{ns}",
        "publish_notebook": f"{NOTEBOOK_DIR}/orchestrate_publish_psv_{ns}",
        "finance_notebook": f"{NOTEBOOK_DIR}/orchestrate_finance_{ns}",
        "ingest_base": f"/Volumes/{CATALOG}/bronze/landing/{ns}/sftp_ingest_poll",
        "parsed_dir": f"{f['landing']}/parsed",
    }


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_lines(lines: list[str]) -> str:
    """Sorted-set digest of record lines; identical construction to the SQL
    sha2(concat_ws(char(10), sort_array(collect_list(line))), 256)."""
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


# --- notebooks ---------------------------------------------------------------
# The parse task runs exactly the parse unit's SQL text (parse_sql.py),
# generated at deploy time and baked in verbatim, plus the empty-input guard
# the composed chain needs: read_files cannot plan over zero matching files,
# and the legacy chain's empty run must still end in a header-only report.
PARSE_NOTEBOOK = r'''# Databricks notebook source
# Orchestrated parse stage (legacy: parse_custbill_fixedwidth.sh, sequenced by
# crontab offsets / run_all.sh). Runs the parse unit's exact SQL text against
# this namespace's ingest incoming/ directory. The chaos widget exists only to
# prove the workflow's dependency-blocking semantics: it fails this task
# before any write.
import re

dbutils.widgets.text("ns", "__NS__")
dbutils.widgets.text("chaos", "none")
ns = dbutils.widgets.get("ns")
chaos = dbutils.widgets.get("chaos")
if ns != "__NS__":
    raise ValueError(f"this notebook's SQL is baked for ns=__NS__, got {ns!r}")
if chaos == "parse":
    raise RuntimeError("chaos=parse: deliberate parse-stage failure (dependency-blocking proof); nothing was written")

LOAD_BRONZE = __LOAD_BRONZE__
BUILD_SILVER = __BUILD_SILVER__
BUILD_QUARANTINE = __BUILD_QUARANTINE__
INCOMING = "/Volumes/ow_tp/bronze/landing/__NS__/sftp_ingest_poll/incoming"
BRONZE = "ow_tp.bronze.custbill_parse_raw___NS__"

# COMMAND ----------
# Same match set as the legacy glob CUSTBILL*.dat. An incoming/ directory with
# no matching files is a legitimate empty batch (legacy: glob matches nothing,
# chain still runs to a header-only report): rewrite this namespace's bronze
# empty so silver/quarantine rebuild empty downstream. read_files over zero
# files would fail the plan, so the guard branches instead of failing.
dbutils.fs.mkdirs(INCOMING)
dats = [f.name for f in dbutils.fs.ls(INCOMING) if re.fullmatch(r"CUSTBILL[^/]*\.dat", f.name)]
if dats:
    spark.sql(LOAD_BRONZE)
else:
    spark.sql(f"DELETE FROM {BRONZE}")
spark.sql(BUILD_SILVER)
spark.sql(BUILD_QUARANTINE)

counts = spark.sql(
    f"SELECT (SELECT count(*) FROM {BRONZE}) AS bronze, "
    "(SELECT count(*) FROM ow_tp.silver.custbill_parsed___NS__) AS silver, "
    "(SELECT count(*) FROM ow_tp.silver.custbill_parse_quarantine___NS__) AS quarantined"
).collect()[0]
print(f"parse done: {len(dats)} file(s), bronze={counts.bronze} silver={counts.silver} quarantined={counts.quarantined}")
'''

# The explicit replacement for the legacy parsed/*.psv filesystem handoff:
# renders silver rows back into the exact legacy record bytes (parse_sql
# PSV_LINE, proven byte-for-byte by the parse unit) and publishes one .psv per
# source file into the finance unit's input directory. Stale artifacts from a
# previous batch are removed so a rerun can never feed finance dead files.
PUBLISH_NOTEBOOK = r'''# Databricks notebook source
# Orchestrated silver -> finance-input handoff (legacy: parse wrote parsed/
# *.psv that finance read on a cron offset 5 minutes later).
import re

dbutils.widgets.text("ns", "__NS__")
ns = dbutils.widgets.get("ns")
if not re.fullmatch(r"[a-z0-9_]{1,24}", ns):
    raise ValueError(f"bad ns: {ns!r}")

silver = f"ow_tp.silver.custbill_parsed_{ns}"
target = f"/Volumes/ow_tp/bronze/landing/{ns}/finance_report/parsed"

rows = spark.sql(f"""
    SELECT source_file, __PSV_LINE__ AS line FROM {silver}
""").collect()
by_file = {}
for r in rows:
    by_file.setdefault(r.source_file, []).append(r.line)

desired = {}
for src, lines in by_file.items():
    if not re.fullmatch(r"CUSTBILL[^/]*\.dat", src):
        raise RuntimeError(f"unexpected source_file in silver: {src!r}")
    # legacy: parsed/$(basename $f .dat).psv; record order is not asserted
    # (sorted deterministic write; finance aggregation is order-insensitive)
    desired[src[: -len(".dat")] + ".psv"] = "\n".join(sorted(lines)) + "\n"

dbutils.fs.mkdirs(target)
for name, text in sorted(desired.items()):
    dbutils.fs.put(f"{target}/{name}", text, True)
for entry in dbutils.fs.ls(target):
    if entry.name.startswith("CUSTBILL") and entry.name.endswith(".psv") and entry.name not in desired:
        dbutils.fs.rm(f"{target}/{entry.name}")
print(f"published {len(desired)} .psv file(s) to {target}")
'''


def parse_notebook_source(n: dict) -> str:
    p = n["parse"]
    return (
        PARSE_NOTEBOOK
        .replace("__LOAD_BRONZE__", repr(S.load_bronze(p)))
        .replace("__BUILD_SILVER__", repr(S.build_silver(p)))
        .replace("__BUILD_QUARANTINE__", repr(S.build_quarantine(p)))
        .replace("__NS__", n["ns"])
    )


def publish_notebook_source(n: dict) -> str:
    return PUBLISH_NOTEBOOK.replace("__PSV_LINE__", S.PSV_LINE).replace("__NS__", n["ns"])


def job_settings(dbx: Databricks, n: dict) -> dict:
    def task(key: str, notebook: str, params: dict, depends: str | None = None) -> dict:
        t = {
            "task_key": key,
            "notebook_task": {"notebook_path": notebook, "base_parameters": params},
            # fail fast and loud: the legacy `|| true` retried nothing and hid
            # everything; the converted chain hides nothing and retries nothing
            "max_retries": 0,
        }
        if depends:
            t["depends_on"] = [{"task_key": depends}]
        return t

    ns = n["ns"]
    return {
        "name": n["job"],
        "tags": {"project": "otterworks-tp", "demo": "batch-estate",
                 "unit": UNIT, "namespace": ns},
        "max_concurrent_runs": 1,
        "queue": {"enabled": True},
        "tasks": [
            task("ingest", n["ingest_notebook"], {"ns": ns}),
            task("parse", n["parse_notebook"],
                 {"ns": ns, "chaos": "{{job.parameters.chaos}}"}, depends="ingest"),
            task("publish_psv", n["publish_notebook"], {"ns": ns}, depends="parse"),
            task("finance", n["finance_notebook"],
                 {"ns": ns, "report_date": "{{job.parameters.report_date}}",
                  "input_subdir": "{{job.parameters.input_subdir}}"}, depends="publish_psv"),
        ],
        "parameters": [
            {"name": "report_date", "default": "2026-01-15"},
            {"name": "input_subdir", "default": "parsed"},
            {"name": "chaos", "default": "none"},
        ],
        # the legacy weekly full-chain cadence (crontab: 0 6 * * 0 run_all.sh),
        # modelled but PAUSED: manual run-now is the demo trigger path
        "schedule": {
            "quartz_cron_expression": "0 0 6 ? * SUN",
            "timezone_id": "UTC",
            "pause_status": "PAUSED",
        },
    }


# --- local commands ----------------------------------------------------------
def cmd_baseline(args) -> int:
    """Derive the golden baseline from what the deterministic legacy run_all.sh
    chain actually produced (drop bytes in, parsed/*.psv and the finance CSV
    out), never from the converted code."""
    root = Path(args.legacy_root)
    empty_root = Path(args.empty_legacy_root)
    drop_src = Path(args.drop_source)

    drop_files = []
    for path in sorted(drop_src.glob("CUSTBILL*.dat")):
        data = path.read_bytes()
        done = root / "incoming" / f"{path.name}.done"
        if done.read_bytes() != data:
            raise SystemExit(f"{path.name}: drop copy differs from the bytes the legacy chain ingested")
        drop_files.append({
            "file_name": path.name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "line_count": len(split_records(data)),
        })
    if not drop_files:
        raise SystemExit(f"no CUSTBILL*.dat under {drop_src}")

    psv_files = {}
    for path in sorted((root / "parsed").glob("CUSTBILL*.psv")):
        lines = [l for l in path.read_text().split("\n") if l]
        psv_files[path.name] = {"rows": len(lines), "sha256_sorted": sha256_lines(lines)}

    reports = sorted((root / "reports").glob("finance_billing_*.csv"))
    empty_reports = sorted((empty_root / "reports").glob("finance_billing_*.csv"))
    if len(reports) != 1 or len(empty_reports) != 1:
        raise SystemExit("expected exactly one finance CSV per legacy root")
    report_text = reports[0].read_text()
    empty_text = empty_reports[0].read_text()
    stamp = reports[0].stem[-8:]
    empty_stamp = empty_reports[0].stem[-8:]
    grid = F.parse_golden(reports[0])
    if sum(v[0] for v in grid.values()) != sum(p["rows"] for p in psv_files.values()):
        raise SystemExit("legacy CSV record counts do not cross-foot with parsed .psv rows")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "unit": UNIT,
        "namespace": args.ns,
        "generated_by": ("scripts/tp_dbx/orchestrate.py baseline (deterministic legacy run_all.sh chain: "
                         "make legacy-etl-gen-data NS=%s; TP_FAKETIME pinned; RUN_ALL_SLEEP=0)" % args.ns),
        "drop_files": drop_files,
        "psv_files": psv_files,
        "report_date": f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}",
        "report_csv": report_text,
        "report_csv_sha256": hashlib.sha256(report_text.encode()).hexdigest(),
        "empty_report_date": f"{empty_stamp[0:4]}-{empty_stamp[4:6]}-{empty_stamp[6:8]}",
        "empty_report_csv": empty_text,
        "empty_report_csv_sha256": hashlib.sha256(empty_text.encode()).hexdigest(),
    }, indent=2) + "\n")
    print(f"wrote {out} ({len(drop_files)} drop files, {len(psv_files)} psv files)")
    return 0


# --- Databricks commands -----------------------------------------------------
def cmd_provision(dbx: Databricks, args) -> int:
    n = names(args.ns)
    for statement in S.provision(n["parse"]):
        dbx.sql_ok(statement)
    F.cmd_provision(dbx, argparse.Namespace(ns=args.ns))
    print(f"provisioned parse+finance tables for ns={args.ns} "
          "(ingest tables are created by the ingest task itself; shared infra untouched)")
    return 0


def cmd_deploy(dbx: Databricks, args) -> int:
    n = names(args.ns)
    ingest_source = (Path(__file__).resolve().parent / "ingest_notebook.py").read_text()
    dbx.import_notebook(n["ingest_notebook"], ingest_source)
    dbx.import_notebook(n["parse_notebook"], parse_notebook_source(n))
    dbx.import_notebook(n["publish_notebook"], publish_notebook_source(n))
    dbx.import_notebook(n["finance_notebook"], F.NOTEBOOK.replace("__NS__", n["ns"]))
    job_id = dbx.upsert_job(job_settings(dbx, n))
    print(f"job {n['job']} = {job_id} (schedule PAUSED, manual run-now): {dbx.host}/jobs/{job_id}")
    return 0


def cmd_land(dbx: Databricks, args) -> int:
    n = names(args.ns)
    files = sorted(Path(args.source_dir).glob("CUSTBILL*.dat"))
    if not files:
        raise SystemExit(f"no CUSTBILL*.dat under {args.source_dir}")
    for path in files:
        dbx.put_file(f"{n['ingest_base']}/drop/{path.name}", path.read_bytes())
        print(f"landed {path.name} -> {n['ingest_base']}/drop/{path.name}")
    return 0


def run_workflow(dbx: Databricks, n: dict, report_date: str, chaos: str = "none") -> dict:
    job = dbx.find_job(n["job"])
    if not job:
        raise SystemExit(f"job {n['job']} not found; run deploy first")
    run_id = dbx.run_job(int(job["job_id"]), {
        "report_date": report_date, "input_subdir": "parsed", "chaos": chaos,
    })
    run = dbx.wait_run(run_id)
    tasks = {t["task_key"]: t.get("state", {}).get("result_state", "")
             for t in run.get("tasks", [])}
    result = run.get("state", {}).get("result_state", "")
    print(f"run {run_id} ({'chaos=' + chaos if chaos != 'none' else 'normal'}): "
          f"{result} {dbx.run_url(run_id)}")
    for key, state in tasks.items():
        print(f"  task {key}: {state}")
    return {"run_id": run_id, "result": result, "tasks": tasks}


def cmd_run(dbx: Databricks, args) -> int:
    n = names(args.ns)
    F.require_date(args.report_date, "report-date")
    outcome = run_workflow(dbx, n, args.report_date, args.chaos)
    return 0 if outcome["result"] == "SUCCESS" else 1


def collect_state(dbx: Databricks, n: dict, baseline: dict, checks: list) -> None:
    """Recompute the composed chain's end state from the target platform and
    compare it to the deterministic legacy baseline."""
    ns, p, f = n["ns"], n["parse"], n["finance"]
    report_date = baseline["report_date"]

    def check(cid, expected, actual, source):
        checks.append({"id": cid, "expected": expected, "actual": actual,
                       "source_of_truth": source,
                       "result": "pass" if expected == actual else "fail"})

    # ingest: staged bytes byte-identical to the legacy drop, registered in bronze
    for item in baseline["drop_files"]:
        name, sha = item["file_name"], item["sha256"]
        data = F.get_volume_bytes(dbx, f"{n['ingest_base']}/incoming/{name}")
        check(f"ingest/staged_sha256/{name}", sha,
              hashlib.sha256(data).hexdigest() if data is not None else "MISSING",
              "legacy drop bytes (baseline) vs Files API GET of staged incoming/ file")
        reg = dbx.sql_ok(
            f"SELECT count(*) FROM {CATALOG}.bronze.custbill_ingest_files_{ns} "
            "WHERE ns = :ns AND file_name = :file_name AND sha256 = :sha256",
            parameters={"ns": ns, "file_name": name, "sha256": sha}).scalar()
        check(f"ingest/bronze_registered/{name}", 1, int(reg),
              f"one registration row per (ns, file, sha) in custbill_ingest_files_{ns}")
    files_ingested = dbx.sql_ok(
        f"SELECT count(DISTINCT source_file) FROM {p.bronze}").scalar()
    check("parse/files_ingested", len(baseline["drop_files"]), int(files_ingested),
          f"baseline drop file count vs DISTINCT source_file in {p.bronze}")

    # parse: silver reproduces the legacy parsed/*.psv records (sorted-set sha)
    silver_by_file = {
        row["source_file"]: row
        for row in dbx.sql_ok(f"""
            SELECT source_file, count(*) AS rows,
                   sha2(concat_ws(char(10), sort_array(collect_list({S.PSV_LINE}))), 256) AS sha
            FROM {p.silver} GROUP BY source_file""").dicts()
    }
    for psv_name, expected in baseline["psv_files"].items():
        dat_name = psv_name[: -len(".psv")] + ".dat"
        row = silver_by_file.get(dat_name)
        check(f"parse/silver_rows/{dat_name}", expected["rows"],
              int(row["rows"]) if row else "MISSING",
              f"legacy parsed/{psv_name} row count vs count(*) in {p.silver}")
        check(f"parse/silver_psv_sha256/{dat_name}", expected["sha256_sorted"],
              row["sha"] if row else "MISSING",
              f"legacy parsed/{psv_name} sorted-set sha256 vs PSV_LINE recomputed from {p.silver}")
    quarantined = dbx.sql_ok(f"SELECT count(*) FROM {p.quarantine}").scalar()
    check("parse/quarantine_rows", 0, int(quarantined),
          "deterministic generator plants no content anomalies for this unit "
          f"(owned by the parse unit's contract); actual from {p.quarantine}")

    # publish: the .psv handoff artifacts equal the legacy parsed output
    for psv_name, expected in baseline["psv_files"].items():
        data = F.get_volume_bytes(dbx, f"{n['parsed_dir']}/{psv_name}")
        lines = [l for l in data.decode().split("\n") if l] if data is not None else None
        check(f"publish/psv_sha256/{psv_name}", expected["sha256_sorted"],
              sha256_lines(lines) if lines is not None else "MISSING",
              f"legacy parsed/{psv_name} sorted-set sha256 vs Files API GET of {n['parsed_dir']}/{psv_name}")

    # finance: gold grid and artifact equal the legacy report to the cent/byte
    grid = F.summary_grid(dbx, f, report_date)
    legacy_grid = {}
    for line in baseline["report_csv"].splitlines()[1:]:
        ccy, rt, cnt, tot = line.split(",")
        legacy_grid[f"{ccy}/{rt}"] = f"{cnt}|{tot}"
    actual_grid = {f"{r[0]}/{r[1]}": f"{int(r[2])}|{Decimal(r[3]):.2f}" for r in grid}
    for key in sorted(set(legacy_grid) | set(actual_grid)):
        check(f"finance/summary/{key}", legacy_grid.get(key, "absent"),
              actual_grid.get(key, "absent"),
              f"legacy finance CSV (baseline) vs {f['summary']} recomputed via SQL")
    crossfoot = dbx.sql_ok(f"""
        WITH s AS (SELECT currency, record_type_code, count(*) c,
                          CAST(sum(amount) AS DECIMAL(18,2)) t
                   FROM {f['silver']}
                   WHERE ns = '{ns}' AND report_date = DATE'{report_date}'
                   GROUP BY currency, record_type_code),
             g AS (SELECT currency, record_type_code, record_count, total_amount
                   FROM {f['summary']}
                   WHERE ns = '{ns}' AND report_date = DATE'{report_date}')
        SELECT count(*) FROM g
        FULL OUTER JOIN s ON g.currency = s.currency AND g.record_type_code = s.record_type_code
        WHERE g.record_count IS DISTINCT FROM s.c OR g.total_amount IS DISTINCT FROM s.t
    """).scalar()
    check("finance/crossfoot-gold-vs-silver", 0, int(crossfoot),
          f"{f['summary']} vs re-aggregated {f['silver']}")
    stamp = report_date.replace("-", "")
    artifact = F.get_volume_bytes(dbx, f"{f['landing']}/reports/finance_billing_{stamp}.csv")
    check("finance/artifact-bytes-equal-legacy", baseline["report_csv"],
          artifact.decode() if artifact is not None else "MISSING",
          "legacy finance CSV bytes (baseline) vs Files API GET of the emitted .csv artifact")
    drow = dbx.sql_ok(
        f"SELECT delivery_status FROM {f['delivery']} "
        f"WHERE ns='{ns}' AND report_date=DATE'{report_date}'").rows
    check("finance/delivery-verified", ["VOLUME_VERIFIED; MAIL=NO_TRANSPORT_CONFIGURED"],
          [r[0] for r in drow], f"{f['delivery']} audit row for (ns, report_date)")

    # empty-input run (executed on the fresh namespace before any file landed)
    empty_date = baseline["empty_report_date"]
    empty_grid = F.summary_grid(dbx, f, empty_date)
    check("empty-input/zero-summary-rows", 0, len(empty_grid),
          f"{f['summary']} slice for the empty-input report date")
    empty_stamp = empty_date.replace("-", "")
    empty_artifact = F.get_volume_bytes(
        dbx, f"{f['landing']}/reports/finance_billing_{empty_stamp}.csv")
    check("empty-input/header-only-artifact", baseline["empty_report_csv"],
          empty_artifact.decode() if empty_artifact is not None else "MISSING",
          "legacy empty-root run_all.sh CSV (baseline) vs Files API GET of the empty-run artifact")


def cmd_recon(dbx: Databricks, args) -> int:
    n = names(args.ns)
    baseline = json.loads(Path(args.baseline).read_text())
    report_date = baseline["report_date"]
    first_pass: list[dict] = []
    chain_checks: list[dict] = []

    def check(cid, expected, actual, source):
        chain_checks.append({"id": cid, "expected": expected, "actual": actual,
                             "source_of_truth": source,
                             "result": "pass" if expected == actual else "fail"})

    collect_state(dbx, n, baseline, first_pass)

    # dependency-blocking proof: chaos-fail the parse task, downstream must be
    # blocked and the end state untouched (run_all.sh would have carried on)
    grid_before = F.summary_grid(dbx, n["finance"], report_date)
    counts_before = dbx.sql_ok(
        f"SELECT (SELECT count(*) FROM {n['parse'].silver}), "
        f"(SELECT count(*) FROM {n['finance']['summary']} WHERE ns='{n['ns']}')").rows
    chaos = run_workflow(dbx, n, report_date, chaos="parse")
    blocked_states = {"UPSTREAM_FAILED", "SKIPPED", "EXCLUDED", "CANCELED", ""}
    check("chain/chaos-parse-task-failed", "FAILED", chaos["tasks"].get("parse", "MISSING"),
          "jobs API task states of the chaos-injected run")
    for key in ("publish_psv", "finance"):
        state = chaos["tasks"].get(key, "MISSING")
        check(f"chain/chaos-{key}-blocked", "blocked",
              "blocked" if state in blocked_states else state,
              f"jobs API task state ({state or 'not started'}); a failed upstream task must block {key}")
    check("chain/chaos-run-failed-loudly", False, chaos["result"] == "SUCCESS",
          "run result of the chaos run: the legacy run_all.sh exited 0 regardless; the workflow must not")
    counts_after = dbx.sql_ok(
        f"SELECT (SELECT count(*) FROM {n['parse'].silver}), "
        f"(SELECT count(*) FROM {n['finance']['summary']} WHERE ns='{n['ns']}')").rows
    check("chain/chaos-no-partial-progress",
          {"grid": grid_before, "counts": counts_before},
          {"grid": F.summary_grid(dbx, n["finance"], report_date), "counts": counts_after},
          "silver/gold state recomputed before and after the chaos run must be identical")

    # idempotency: an actual full-workflow rerun, then every check recomputed
    rerun = run_workflow(dbx, n, report_date)
    rerun_checks: list[dict] = []
    collect_state(dbx, n, baseline, rerun_checks)
    identical = [(c["id"], c["actual"]) for c in rerun_checks] == \
                [(c["id"], c["actual"]) for c in first_pass]
    rerun_green = rerun["result"] == "SUCCESS" and all(c["result"] == "pass" for c in rerun_checks)
    idempotency = {
        "performed": True,
        "result": "pass" if identical and rerun_green else "fail",
        "evidence": (f"full workflow rerun (run {rerun['run_id']}, all tasks SUCCESS) then all "
                     f"{len(rerun_checks)} state checks recomputed from the platform and byte-identical "
                     "to the first pass"
                     if identical and rerun_green else
                     f"rerun result={rerun['result']}; state identical={identical}"),
    }
    checks = chain_checks + rerun_checks
    report = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": n["ns"],
        "generated_at": utcnow(),
        "run_mode": "live",
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": idempotency,
        "planted_anomaly_detections": {
            "expected_set": [["chaos=parse", "parse FAILED; publish_psv and finance blocked; no state change"]],
            "actual_set": [["chaos=parse",
                            "parse FAILED; publish_psv and finance blocked; no state change"]]
            if all(c["result"] == "pass" for c in checks if c["id"].startswith("chain/")) else
            [["chaos=parse", "see failing chain/* checks"]],
            "missing": [] if all(c["result"] == "pass" for c in checks if c["id"].startswith("chain/"))
            else [["chaos=parse", "blocking semantics not proven"]],
            "unexpected": [],
        },
        "unverified_paths": [
            "cron time-offset scheduling (*/15, 5-59/15, 02:10) is retired, not reproduced: the workflow replaces it with explicit depends_on edges; the weekly run_all cadence exists only as a PAUSED schedule",
            "run_all.sh RUN_ALL_SLEEP inter-stage sleeps have no platform equivalent (dependencies are event-driven, not time-guessed)",
            "content-level anomalies (bad dates, non-numeric amounts, trailer mismatches) are not planted here: quarantine/parity coverage for them is owned by the parse unit's contract (this run's generator output is clean, asserted by parse/quarantine_rows=0)",
            "legacy .psv record order is not asserted (sorted-set sha comparison; the finance aggregation is order-insensitive, matching the parse unit's declared approach)",
            "mail transport: no SMTP exists in the demo workspace; delivery is volume-verified and recorded explicitly",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    failed = [c["id"] for c in checks if c["result"] != "pass"]
    print(f"recon: {len(checks)} checks, {len(failed)} failed, idempotency={idempotency['result']} -> {out}")
    for cid in failed:
        print(f"  FAIL {cid}")
    return 1 if failed or idempotency["result"] == "fail" else 0


def cmd_clean(dbx: Databricks, args) -> int:
    """Remove this namespace's landed volume files. Tables, the workflow and
    the committed recon evidence are the unit's persistent slice and are kept."""
    n = names(args.ns)
    bases = [f"{n['ingest_base']}/{sub}" for sub in ("drop", "incoming", "archive", ".staging")]
    bases += [f"{n['finance']['landing']}/{sub}" for sub in ("parsed", "reports")]
    for base in bases:
        for entry in dbx.list_dir(base):
            if not entry.get("is_directory"):
                dbx.delete_file(entry["path"])
                print(f"deleted {entry['path']}")
        dbx.delete_dir(base)
    dbx.delete_dir(n["ingest_base"])
    dbx.delete_dir(n["finance"]["landing"])
    print(f"cleaned landed files for ns={n['ns']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ns", default="cnvorch")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--legacy-root", required=True)
    b.add_argument("--empty-legacy-root", required=True)
    b.add_argument("--drop-source", required=True)
    b.add_argument("--out", required=True)
    sub.add_parser("provision")
    sub.add_parser("deploy")
    land = sub.add_parser("land")
    land.add_argument("--source-dir", required=True)
    run = sub.add_parser("run")
    run.add_argument("--report-date", required=True)
    run.add_argument("--chaos", default="none")
    recon = sub.add_parser("recon")
    recon.add_argument("--baseline", required=True)
    recon.add_argument("--out", required=True)
    sub.add_parser("clean")
    args = p.parse_args()
    require_ns(args.ns)
    if args.cmd == "baseline":
        return cmd_baseline(args)
    dbx = Databricks()
    return {
        "provision": cmd_provision,
        "deploy": cmd_deploy,
        "land": cmd_land,
        "run": cmd_run,
        "recon": cmd_recon,
        "clean": cmd_clean,
    }[args.cmd](dbx, args)


if __name__ == "__main__":
    raise SystemExit(main())

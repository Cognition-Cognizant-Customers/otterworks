#!/usr/bin/env python3
"""Convert parse_custbill_fixedwidth.sh into a schema-validated Databricks
silver transform with an explicit quarantine table (namespace-scoped, ow_tp).

The legacy parser dropped HDR/TRL lines, cut fixed-width columns, trimmed
trailing spaces, reformatted dates by digit insertion, coerced amounts with
awk arithmetic, and only *logged* the trailer count. Bad dates and
non-numeric amounts flowed through silently; trailer mismatches were noise
on stdout. The converted job preserves the legacy output byte-for-byte for
valid records and turns every silently-mishandled record into an explicit
quarantine row.

Workflow (see docs/tech-partnerships/contracts/parse_custbill_fixedwidth-<ns>.contract.json):

  plant         mutate the generated drop files with the planted anomalies
  baseline      derive the golden expectations from the actual legacy run
  provision     create the namespace's tables (shared infra is parent-owned)
  land          upload the exact legacy input bytes to the landing volume
  expectations  load the baseline's expected values
  run           bronze -> silver + quarantine
  job           create/refresh the (manual-trigger) Databricks job
  run-job       trigger the job and wait
  recon         recompute checks from the target, emit a schema-valid report
  status        summarise what exists in the namespace
  teardown      drop the namespace's objects and landed files
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse_sql as S
from client import Databricks, DbxError, require_ident, require_ns

REPO = Path(__file__).resolve().parents[2]
WORKSPACE_DIR = "/Shared/ow_tp"

# Deterministic anomaly plan: one silently-mishandled record class per file.
BAD_DATE_BODY_ROW = 7        # 1-based body row in file 1: impossible calendar date
BAD_DATE_VALUE = "20241131"  # legacy emits "2024-11-31" without blinking
BAD_AMOUNT_BODY_ROW = 13     # 1-based body row in file 2: non-numeric amount
BAD_AMOUNT_VALUE = "0000012X4567"  # awk coerces the numeric prefix -> "0.12"
TRAILER_DELTA = 3            # file 3: trailer overstates the body count


def names(args) -> S.Names:
    return S.Names(catalog=require_ident(args.catalog, "catalog"), ns=require_ns(args.ns))


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def sha256_lines(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def manifest_path(root: Path, ns: str) -> Path:
    return root / f"parse_plant_{ns}.json"


def baseline_path(args) -> Path:
    if args.baseline:
        return Path(args.baseline)
    return REPO / f"docs/tech-partnerships/baselines/parse_custbill_fixedwidth-{args.ns}.baseline.json"


def load_baseline(args) -> dict:
    path = baseline_path(args)
    if not path.exists():
        raise SystemExit(f"baseline not found: {path}; run plant + the legacy job + baseline first")
    return json.loads(path.read_text())


# --- local (legacy-side) commands -------------------------------------------
def cmd_plant(args) -> int:
    """Mutate the deterministic generator output in the SFTP drop directory so
    the run carries exactly the anomaly classes the legacy parser mishandles.
    Runs before ingest; never touches etl/legacy-extra."""
    n = names(args)
    drop = Path(args.legacy_root) / "sftp-drop/upload"
    files = sorted(drop.glob(f"CUSTBILL_{n.ns.upper()}_*.dat"))
    if len(files) < 3:
        raise SystemExit(f"need at least 3 generated drops in {drop}; run make legacy-etl-gen-data NS={n.ns} (NFILES>=3)")
    planted = []

    def rewrite(path: Path, mutate) -> None:
        lines = path.read_text().split("\n")
        mutate(lines)
        path.write_text("\n".join(lines))

    def body_line_index(lines: list[str], body_row: int) -> int:
        row = 0
        for i, line in enumerate(lines):
            if not line or line.startswith(("HDR", "TRL")):
                continue
            row += 1
            if row == body_row:
                return i
        raise SystemExit(f"body row {body_row} not found")

    def plant_bad_date(lines: list[str]) -> None:
        i = body_line_index(lines, BAD_DATE_BODY_ROW)
        planted.append({"file": files[0].name, "kind": "invalid_calendar_date",
                        "cust_id": lines[i][:10].rstrip(), "body_row": BAD_DATE_BODY_ROW,
                        "detail": f"bill_date={BAD_DATE_VALUE}"})
        lines[i] = lines[i][:40] + BAD_DATE_VALUE + lines[i][48:]

    def plant_bad_amount(lines: list[str]) -> None:
        i = body_line_index(lines, BAD_AMOUNT_BODY_ROW)
        planted.append({"file": files[1].name, "kind": "nonnumeric_amount",
                        "cust_id": lines[i][:10].rstrip(), "body_row": BAD_AMOUNT_BODY_ROW,
                        "detail": f"amount={BAD_AMOUNT_VALUE}"})
        lines[i] = lines[i][:48] + BAD_AMOUNT_VALUE + lines[i][60:]

    def plant_trailer_mismatch(lines: list[str]) -> None:
        for i, line in enumerate(lines):
            if line.startswith("TRL"):
                count = int(line[3:13])
                lines[i] = "TRL" + f"{count + TRAILER_DELTA:010d}" + line[13:]
                planted.append({"file": files[2].name, "kind": "trailer_count_mismatch",
                                "cust_id": "", "body_row": None,
                                "detail": f"trailer={count + TRAILER_DELTA} body={count}"})
                return
        raise SystemExit("no TRL line found")

    rewrite(files[0], plant_bad_date)
    rewrite(files[1], plant_bad_amount)
    rewrite(files[2], plant_trailer_mismatch)
    out = manifest_path(Path(args.legacy_root), n.ns)
    out.write_text(json.dumps({"namespace": n.ns, "planted_anomalies": planted}, indent=2) + "\n")
    print(f"planted {len(planted)} anomalies; manifest: {out}")
    return 0


def _parse_body(line: str) -> dict:
    return {
        "cust_id": line[0:10].rstrip(),
        "cust_name": line[10:40].rstrip(),
        "date_raw": line[40:48],
        "amount_raw": line[48:60],
        "currency": line[60:63].rstrip(),
        "record_type": line[63:65],
    }


def _is_valid(f: dict) -> bool:
    if not 1 <= len(f["cust_id"]) <= 10:
        return False
    if not (len(f["amount_raw"]) == 12 and f["amount_raw"].isdigit()):
        return False
    try:
        d = f["date_raw"]
        date(int(d[0:4]), int(d[4:6]), int(d[6:8]))
    except ValueError:
        return False
    return f["currency"] in ("USD", "EUR", "GBP") and f["record_type"] in ("01", "02")


def cmd_baseline(args) -> int:
    """Derive the golden expectations from what the legacy script actually
    produced (incoming/*.dat.done inputs + parsed/*.psv outputs), not from the
    generator or from the converted code."""
    n = names(args)
    root = Path(args.legacy_root)
    manifest = json.loads(manifest_path(root, n.ns).read_text())
    planted = manifest["planted_anomalies"]
    done = sorted((root / "incoming").glob(f"CUSTBILL_{n.ns.upper()}_*.dat.done"))
    if not done:
        raise SystemExit(f"no processed inputs under {root}/incoming; run the legacy chain first")

    checks: dict[str, str] = {}
    files_evidence = {}
    totals: dict[tuple[str, str], list[int]] = {}
    grand = [0, 0]
    passthrough = []

    for path in done:
        fname = path.name[: -len(".done")]
        input_lines = [l for l in path.read_text().split("\n") if l.strip()]
        body = [l for l in input_lines if not l.startswith(("HDR", "TRL"))]
        psv = [l for l in (root / "parsed" / (Path(fname).stem + ".psv")).read_text().split("\n") if l]
        if len(psv) != len(body):
            raise SystemExit(f"{fname}: psv rows {len(psv)} != body rows {len(body)}")

        bad_rows = {p["body_row"] for p in planted if p["file"] == fname and p["body_row"]}
        valid_psv, invalid_seen = [], set()
        for row, (raw, out_line) in enumerate(zip(body, psv), start=1):
            fields = _parse_body(raw)
            if _is_valid(fields):
                expect = "|".join([
                    fields["cust_id"], fields["cust_name"],
                    f"{fields['date_raw'][0:4]}-{fields['date_raw'][4:6]}-{fields['date_raw'][6:8]}",
                    "%d.%02d" % divmod(int(fields["amount_raw"]), 100),
                    fields["currency"], fields["record_type"],
                ])
                if out_line != expect:
                    raise SystemExit(f"{fname} row {row}: reconstruction '{expect}' != legacy '{out_line}'")
                valid_psv.append(out_line)
                key = (fields["currency"], fields["record_type"])
                totals.setdefault(key, [0, 0])
                totals[key][0] += 1
                totals[key][1] += int(fields["amount_raw"])
                grand[0] += 1
                grand[1] += int(fields["amount_raw"])
            else:
                invalid_seen.add(row)
                passthrough.append({"file": fname, "body_row": row, "legacy_emitted": out_line})
        if invalid_seen != bad_rows:
            raise SystemExit(f"{fname}: invalid rows {sorted(invalid_seen)} != planted {sorted(bad_rows)}")

        checks[f"input_sha256/{fname}"] = sha256_lines(input_lines)
        checks[f"file_valid_rows/{fname}"] = str(len(valid_psv))
        checks[f"file_valid_sha256/{fname}"] = sha256_lines(valid_psv)
        files_evidence[fname] = {
            "legacy_psv_rows": len(psv),
            "legacy_psv_sha256_sorted": sha256_lines(psv),
            "valid_rows": len(valid_psv),
        }

    for (ccy, rt), (count, cents) in sorted(totals.items()):
        checks[f"totals/{ccy}/{rt}"] = f"{count}|{cents}"
    checks["grand_total"] = f"{grand[0]}|{grand[1]}"
    checks["files_ingested"] = str(len(done))
    checks["quarantine_rows"] = str(len(planted))

    out = baseline_path(args)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "unit": "parse_custbill_fixedwidth",
        "namespace": n.ns,
        "generated_by": "scripts/tp_dbx/parse_unit.py baseline (deterministic legacy run, TZ=UTC LC_ALL=C)",
        "seed": f"gen_sample_data.pl {n.ns} (deterministic per namespace) + parse_unit.py plant",
        "planted_anomalies": planted,
        "legacy_passthrough": passthrough,
        "files": files_evidence,
        "checks": checks,
    }, indent=2, sort_keys=False) + "\n")
    print(f"wrote {out} ({len(checks)} expected checks, {len(planted)} planted anomalies)")
    return 0


# --- Databricks-side commands ------------------------------------------------
def cmd_provision(dbx: Databricks, args) -> int:
    n = names(args)
    for statement in S.provision(n):
        dbx.sql_ok(statement)
    print(f"provisioned namespace tables for ns={n.ns} (shared catalog/schemas/volume untouched)")
    return 0


def cmd_land(dbx: Databricks, args) -> int:
    """Upload the exact bytes the legacy run consumed (incoming/*.dat.done,
    renamed back to *.dat) through the verified Files API transport."""
    n = names(args)
    done = sorted((Path(args.legacy_root) / "incoming").glob(f"CUSTBILL_{n.ns.upper()}_*.dat.done"))
    if not done:
        raise SystemExit("no processed legacy inputs to land")
    for path in done:
        dbx.put_file(f"{n.drop_dir}/{path.name[: -len('.done')]}", path.read_bytes())
    print(f"landed {len(done)} drops under {n.drop_dir}")
    return 0


def cmd_expectations(dbx: Databricks, args) -> int:
    n = names(args)
    checks = load_baseline(args)["checks"]
    rows = ",\n".join(f"('{esc(k)}', '{esc(v)}')" for k, v in sorted(checks.items()))
    dbx.sql_ok(f"INSERT OVERWRITE {n.expectations} (check_id, expected) VALUES\n{rows}")
    print(f"loaded {len(checks)} expectations into {n.expectations}")
    return 0


def _transform(dbx: Databricks, n: S.Names) -> None:
    dbx.sql_ok(S.load_bronze(n))
    dbx.sql_ok(S.build_silver(n))
    dbx.sql_ok(S.build_quarantine(n))


def cmd_run(dbx: Databricks, args) -> int:
    n = names(args)
    _transform(dbx, n)
    summary = dbx.sql_ok(
        f"SELECT (SELECT count(*) FROM {n.bronze}) AS bronze_rows, "
        f"(SELECT count(DISTINCT source_file) FROM {n.bronze}) AS files, "
        f"(SELECT count(*) FROM {n.silver}) AS silver_rows, "
        f"(SELECT count(*) FROM {n.quarantine}) AS quarantined"
    )
    print(json.dumps(summary.dicts()[0], indent=2))
    return 0


def _sql_task_files(n: S.Names) -> dict[str, str]:
    return {
        f"{WORKSPACE_DIR}/parse_load_bronze_{n.ns}.sql": S.load_bronze(n),
        f"{WORKSPACE_DIR}/parse_build_silver_{n.ns}.sql": S.build_silver(n),
        f"{WORKSPACE_DIR}/parse_build_quarantine_{n.ns}.sql": S.build_quarantine(n),
        f"{WORKSPACE_DIR}/parse_recon_gate_{n.ns}.sql": S.recon_gate(n),
    }


def cmd_job(dbx: Databricks, args) -> int:
    """Manual-trigger job (no schedule at all): bronze -> silver -> quarantine
    -> recon gate, every task on the existing serverless SQL warehouse."""
    n = names(args)
    import base64
    dbx.ok("POST", "/api/2.0/workspace/mkdirs", {"path": WORKSPACE_DIR})
    for path, text in _sql_task_files(n).items():
        dbx.ok("POST", "/api/2.0/workspace/import", {
            "path": path, "format": "AUTO", "overwrite": True,
            "content": base64.b64encode(text.encode()).decode(),
        })

    def sql_task(key: str, depends: str | None = None) -> dict:
        task = {
            "task_key": key,
            "sql_task": {
                "warehouse_id": dbx.warehouse_id,
                "file": {"path": f"{WORKSPACE_DIR}/parse_{key}_{n.ns}.sql", "source": "WORKSPACE"},
            },
        }
        if depends:
            task["depends_on"] = [{"task_key": depends}]
        return task

    settings = {
        "name": n.job_name,
        "tags": {"project": "otterworks-tp", "demo": "parse-fixedwidth", "namespace": n.ns},
        "max_concurrent_runs": 1,
        "tasks": [
            sql_task("load_bronze"),
            sql_task("build_silver", "load_bronze"),
            sql_task("build_quarantine", "build_silver"),
            sql_task("recon_gate", "build_quarantine"),
        ],
        "queue": {"enabled": True},
    }
    job_id = dbx.upsert_job(settings)
    print(f"job {job_id} ({n.job_name}, manual trigger only): {dbx.host}/jobs/{job_id}")
    return 0


def cmd_run_job(dbx: Databricks, args) -> int:
    n = names(args)
    job = dbx.find_job(n.job_name)
    if not job:
        raise SystemExit(f"job {n.job_name} not found; run job first")
    run_id = dbx.run_job(int(job["job_id"]))
    print(f"triggered run: {dbx.run_url(run_id)}")
    run = dbx.wait_run(run_id)
    state = run.get("state", {})
    print(f"result: {state.get('result_state')} — {str(state.get('state_message'))[:400]}")
    for task in run.get("tasks", []):
        print(f"  task {task['task_key']}: {task.get('state', {}).get('result_state')}")
    return 0 if state.get("result_state") == "SUCCESS" else 1


def cmd_recon(dbx: Databricks, args) -> int:
    n = names(args)
    baseline = load_baseline(args)
    run_id = uuid.uuid4().hex[:12]
    checks = dbx.sql_ok(S.recon_checks(n)).dicts()

    # the report schema pins idempotency_rerun.performed to true, so the rerun
    # is not optional: the whole transform is executed again by observation
    _transform(dbx, n)
    rerun = dbx.sql_ok(S.recon_checks(n)).dicts()
    same = rerun == checks
    idempotency = {
        "performed": True,
        "result": "pass" if same else "fail",
        "evidence": (f"bronze/silver/quarantine rebuilt end-to-end; all {len(checks)} "
                     "checks byte-identical" if same else "check values changed on rerun"),
    }
    checks = rerun

    expected_anomalies = sorted(
        [a["file"], a["kind"], a["cust_id"]] for a in baseline["planted_anomalies"]
    )
    actual_anomalies = sorted(
        [row["source_file"], row["reason"], row["cust_id"]]
        for row in dbx.sql_ok(S.anomaly_set(n)).dicts()
    )
    expected_keys = {tuple(a) for a in expected_anomalies}
    actual_keys = {tuple(a) for a in actual_anomalies}

    report = {
        "kind": "recon-report",
        "unit": "parse_custbill_fixedwidth",
        "namespace": n.ns,
        "generated_at": now(),
        "run_mode": args.run_mode,
        "checks": [
            {
                "id": c["check_id"],
                "expected": c["expected"],
                "actual": c["actual"],
                "source_of_truth": "deterministic legacy run of parse_custbill_fixedwidth.sh (committed baseline)",
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
            "legacy stdout log line ('parsed N records (trailer says M)') is not reproduced; the trailer mismatch is a quarantine row instead",
            "legacy output file ordering (input order) is not asserted; per-record bytes are compared as sorted sets via sha256",
            "legacy .done rename and /tmp lockfile side effects have no platform equivalent",
        ],
    }
    failed = [c for c in report["checks"] if c["result"] == "fail"]
    rows = ",".join(
        f"('{run_id}', current_timestamp(), '{esc(c['id'])}', '{esc(str(c['expected']))}', "
        f"'{esc(str(c['actual']))}', '{esc(c['result'])}')"
        for c in report["checks"]
    )
    dbx.sql_ok(f"INSERT INTO {n.recon_runs} VALUES {rows}")

    out = Path(args.out) if args.out else REPO / f"docs/tech-partnerships/recon/parse_custbill_fixedwidth-{n.ns}.recon.json"
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


def cmd_status(dbx: Databricks, args) -> int:
    n = names(args)
    result = dbx.sql(
        f"SELECT (SELECT count(*) FROM {n.bronze}) AS bronze_rows, "
        f"(SELECT count(DISTINCT source_file) FROM {n.bronze}) AS files, "
        f"(SELECT count(*) FROM {n.silver}) AS silver_rows, "
        f"(SELECT count(*) FROM {n.quarantine}) AS quarantined, "
        f"(SELECT count(*) FROM {n.expectations}) AS expectation_rows"
    )
    print(json.dumps(result.dicts()[0] if result.ok else {"state": result.state, "error": result.error}, indent=2))
    job = dbx.find_job(n.job_name)
    if job:
        detail = dbx.ok("GET", f"/api/2.1/jobs/get?job_id={int(job['job_id'])}")
        schedule = detail.get("settings", {}).get("schedule")
        state = schedule.get("pause_status", "UNKNOWN") if schedule else "NO SCHEDULE (manual only)"
        print(f"job: {dbx.host}/jobs/{job['job_id']} schedule={state}")
    else:
        print("job: absent")
    return 0


def cmd_teardown(dbx: Databricks, args) -> int:
    n = names(args)
    for table in (n.quarantine, n.silver, n.bronze, n.expectations, n.recon_runs):
        dbx.sql_ok(f"DROP TABLE IF EXISTS {table}")
        print(f"dropped {table}")
    for entry in dbx.list_dir(n.drop_dir):
        dbx.delete_file(entry.get("path", ""))
    dbx.delete_dir(n.drop_dir)
    dbx.delete_dir(n.landing)
    job = dbx.find_job(n.job_name)
    if job:
        dbx.ok("POST", "/api/2.0/jobs/delete", {"job_id": int(job["job_id"])})
        print(f"deleted job {job['job_id']}")
    for path in _sql_task_files(n):
        status, _ = dbx.call("POST", "/api/2.0/workspace/delete", {"path": path})
        print(f"workspace delete {path}: HTTP {status}")
    # sql_ok, not sql: an errored scan returns no rows, which would read as
    # proof of absence
    leftovers = {
        "bronze_tables": dbx.sql_ok(f"SHOW TABLES IN {n.catalog}.bronze LIKE 'custbill_parse_*_{n.ns}'").rows,
        "silver_tables": dbx.sql_ok(f"SHOW TABLES IN {n.catalog}.silver LIKE 'custbill_parse*_{n.ns}'").rows,
        "ops_tables": dbx.sql_ok(f"SHOW TABLES IN {n.catalog}.ops LIKE 'parse_*_{n.ns}'").rows,
        "job": dbx.find_job(n.job_name) is not None,
        "landed_paths": [e.get("path") for e in dbx.list_dir(n.drop_dir)],
    }
    print("negative verification: " + json.dumps(leftovers))
    survivors = [k for k, v in leftovers.items() if v]
    if survivors:
        print(f"teardown incomplete, survivors: {survivors}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ns", default="cnvparse")
    parser.add_argument("--catalog", default="ow_tp")
    parser.add_argument("--legacy-root", default="/tmp/otterworks-legacy")
    parser.add_argument("--baseline", default="")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("plant", "baseline", "provision", "land", "expectations",
                 "run", "job", "run-job", "status", "teardown"):
        sub.add_parser(name)
    recon = sub.add_parser("recon")
    recon.add_argument("--out", default="")
    recon.add_argument("--run-mode", default="live", choices=["fixture", "live"])

    args = parser.parse_args()
    local = {"plant": cmd_plant, "baseline": cmd_baseline}
    if args.command in local:
        return local[args.command](args)
    commands = {
        "provision": cmd_provision, "land": cmd_land, "expectations": cmd_expectations,
        "run": cmd_run, "job": cmd_job, "run-job": cmd_run_job, "recon": cmd_recon,
        "status": cmd_status, "teardown": cmd_teardown,
    }
    try:
        return commands[args.command](Databricks(), args)
    except DbxError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

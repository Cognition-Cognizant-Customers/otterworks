#!/usr/bin/env python3
"""Deploy, run, and reconcile the converted SFTP-ingest unit (sftp_ingest_poll.ksh).

Converted target: job ow_tp_ingest_<ns> — atomic Databricks landing + bronze
registration on the namespace slice /Volumes/ow_tp/bronze/landing/<ns> and
tables ow_tp.bronze.custbill_{ingest_files,raw}_<ns>. Stdlib only, reusing the
shared client in scripts/tp_dbx/client.py.

Usage:
  python3 scripts/tp_dbx/ingest.py deploy --ns cnvingest
  python3 scripts/tp_dbx/ingest.py land   --ns cnvingest --source-dir <dir> [--plant-anomalies]
  python3 scripts/tp_dbx/ingest.py run    --ns cnvingest
  python3 scripts/tp_dbx/ingest.py recon  --ns cnvingest --golden <golden.json> \
      --source-dir <dir> --out <report.recon.json>

The recon always performs the idempotency rerun: the report is audit evidence and
the recon-report schema pins idempotency_rerun.performed to true.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from client import Databricks, require_ns  # noqa: E402

NOTEBOOK_DIR = "/Shared/ow_tp"
LANDING = "/Volumes/ow_tp/bronze/landing"


def base(ns: str) -> str:
    return f"{LANDING}/{ns}"


def job_settings(ns: str, notebook_path: str) -> dict:
    return {
        "name": f"ow_tp_ingest_{ns}",
        "tags": {"project": "otterworks-tp", "demo": "batch-estate", "unit": "sftp_ingest_poll", "namespace": ns},
        "max_concurrent_runs": 1,
        "tasks": [
            {
                "task_key": "ingest_poll",
                "notebook_task": {
                    "notebook_path": notebook_path,
                    "base_parameters": {"ns": ns},
                },
            }
        ],
    }


def cmd_deploy(dbx: Databricks, args) -> int:
    source = (Path(__file__).resolve().parent / "ingest_notebook.py").read_text()
    notebook_path = f"{NOTEBOOK_DIR}/ingest_{args.ns}"
    dbx.import_notebook(notebook_path, source)
    job_id = dbx.upsert_job(job_settings(args.ns, notebook_path))
    print(f"job ow_tp_ingest_{args.ns} (manual trigger only): {dbx.host}/jobs/{job_id}")
    print(f"notebook: {notebook_path}")
    return 0


def cmd_land(dbx: Databricks, args) -> int:
    drop = f"{base(args.ns)}/drop"
    count = 0
    for path in sorted(Path(args.source_dir).glob("CUSTBILL*.dat")):
        dbx.put_file(f"{drop}/{path.name}", path.read_bytes())
        print(f"landed {path.name} -> {drop}/{path.name}")
        count += 1
    if args.plant_anomalies:
        dbx.put_file(f"{drop}/NOTCUSTBILL_x.txt", b"junk\n")
        dbx.put_file(f"{drop}/CUSTBILL_PARTIAL_999.dat.filepart", b"partial\n")
        print("planted anomalies: NOTCUSTBILL_x.txt, CUSTBILL_PARTIAL_999.dat.filepart")
    print(f"landed {count} file(s)")
    return 0


def run_job(dbx: Databricks, ns: str) -> None:
    job = dbx.find_job(f"ow_tp_ingest_{ns}")
    if not job:
        raise SystemExit(f"job ow_tp_ingest_{ns} not found; deploy first")
    run_id = dbx.run_job(int(job["job_id"]))
    run = dbx.wait_run(run_id)
    result = run.get("state", {}).get("result_state")
    print(f"run {run_id}: {result} ({dbx.run_url(run_id)})")
    if result != "SUCCESS":
        raise SystemExit(f"job run failed: {run.get('state')}")


def cmd_run(dbx: Databricks, args) -> int:
    run_job(dbx, args.ns)
    return 0


def get_file_bytes(dbx: Databricks, path: str) -> bytes | None:
    import urllib.parse, urllib.request, urllib.error

    quoted = urllib.parse.quote(path, safe="/")
    req = urllib.request.Request(
        dbx.host + f"/api/2.0/fs/files{quoted}",
        headers={"Authorization": f"Bearer {dbx.token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def collect_state(dbx: Databricks, ns: str, golden: dict, source_dir: Path) -> list[dict]:
    b = base(ns)
    checks: list[dict] = []
    incoming_names = {e["name"] for e in dbx.list_dir(f"{b}/incoming")}
    archive_names = {e["name"] for e in dbx.list_dir(f"{b}/archive")}
    drop_entries = [e["name"] for e in dbx.list_dir(f"{b}/drop")]

    for item in golden["drop_files"]:
        name, sha = item["file_name"], item["sha256"]
        data = get_file_bytes(dbx, f"{b}/incoming/{name}")
        actual_sha = hashlib.sha256(data).hexdigest() if data is not None else "MISSING"
        checks.append({
            "id": f"staged_sha/{name}",
            "expected": sha,
            "actual": actual_sha,
            "source_of_truth": "golden baseline from deterministic legacy sftp_ingest_poll.ksh run (NS=cnvingest); actual sha256 recomputed from Files API GET of the staged volume file",
            "result": "pass" if actual_sha == sha else "fail",
        })
        arc = f"{name}.{sha[:16]}"
        checks.append({
            "id": f"archive_copy/{name}",
            "expected": arc,
            "actual": arc if arc in archive_names else "MISSING",
            "source_of_truth": "contract archive_copy check; actual from Files API directory listing of archive/",
            "result": "pass" if arc in archive_names else "fail",
        })
        local = source_dir / name
        line_count = len(local.read_bytes().decode("latin-1").splitlines())
        row = dbx.sql_ok(
            f"SELECT sha256, bytes, line_count FROM ow_tp.bronze.custbill_ingest_files_{ns} "
            f"WHERE ns = '{ns}' AND file_name = '{name}'"
        ).rows
        expected_reg = f"{sha}|{item['bytes']}|{line_count}|1row"
        actual_reg = f"{row[0][0]}|{row[0][1]}|{row[0][2]}|{len(row)}row" if row else "MISSING"
        checks.append({
            "id": f"bronze_files_row/{name}",
            "expected": expected_reg,
            "actual": actual_reg,
            "source_of_truth": "golden baseline bytes/sha + local golden line count; actual recomputed via SQL from ow_tp.bronze.custbill_ingest_files_" + ns,
            "result": "pass" if actual_reg == expected_reg else "fail",
        })
        raw = dbx.sql_ok(
            f"SELECT COUNT(*) FROM ow_tp.bronze.custbill_raw_{ns} "
            f"WHERE ns = '{ns}' AND file_name = '{name}' AND sha256 = '{sha}'"
        ).scalar()
        checks.append({
            "id": f"bronze_raw_count/{name}",
            "expected": str(line_count),
            "actual": str(raw),
            "source_of_truth": "line count of the golden baseline file; actual recomputed via SQL COUNT(*) from ow_tp.bronze.custbill_raw_" + ns,
            "result": "pass" if str(raw) == str(line_count) else "fail",
        })

    remaining_dats = sorted(n for n in drop_entries if n.startswith("CUSTBILL") and n.endswith(".dat"))
    checks.append({
        "id": "drop_deleted_after_stage",
        "expected": "no CUSTBILL*.dat remaining in drop",
        "actual": "none" if not remaining_dats else f"remaining: {remaining_dats}",
        "source_of_truth": "legacy behaviour (source deleted once staged); actual from Files API directory listing of drop/",
        "result": "pass" if not remaining_dats else "fail",
    })
    for planted in ("NOTCUSTBILL_x.txt", "CUSTBILL_PARTIAL_999.dat.filepart"):
        untouched = planted in drop_entries and planted not in incoming_names
        checks.append({
            "id": f"non_matching_ignored/{planted}",
            "expected": "left untouched in drop, not staged",
            "actual": "left untouched in drop, not staged" if untouched else f"in_drop={planted in drop_entries}, staged={planted in incoming_names}",
            "source_of_truth": "legacy glob behaviour (CUSTBILL*.dat only); actual from Files API listings of drop/ and incoming/",
            "result": "pass" if untouched else "fail",
        })
    return checks


def cmd_recon(dbx: Databricks, args) -> int:
    golden = json.loads(Path(args.golden).read_text())
    source_dir = Path(args.source_dir)
    checks = collect_state(dbx, args.ns, golden, source_dir)

    # The rerun is not optional: the recon-report schema pins
    # idempotency_rerun.performed to true and requires a result.
    run_job(dbx, args.ns)
    rerun_checks = collect_state(dbx, args.ns, golden, source_dir)
    identical = [(c["id"], c["actual"]) for c in checks] == [(c["id"], c["actual"]) for c in rerun_checks]
    rerun_green = all(c["result"] == "pass" for c in rerun_checks)
    idempotency = {
        "performed": True,
        "result": "pass" if identical and rerun_green else "fail",
        "evidence": f"job re-run with empty drop; all {len(rerun_checks)} checks recomputed from the platform and byte-identical to the first pass" if identical else "rerun state diverged from first pass",
    }

    expected_set = [[p, "not_staged"] for p in ("NOTCUSTBILL_x.txt", "CUSTBILL_PARTIAL_999.dat.filepart")]
    actual_set = [[c["id"].split("/", 1)[1], "not_staged"] for c in checks if c["id"].startswith("non_matching_ignored/") and c["result"] == "pass"]
    missing = [e for e in expected_set if e not in actual_set]
    unexpected = [a for a in actual_set if a not in expected_set]

    report = {
        "kind": "recon-report",
        "unit": "sftp_ingest_poll",
        "namespace": args.ns,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": idempotency,
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": missing,
            "unexpected": unexpected,
        },
        "unverified_paths": [
            "real mainframe SFTP transfer (demo estate lands files via the verified Files API transport instead)",
            "crontab scheduling (converted job is manual-trigger only per demo rules; legacy ran every 15 min)",
        ],
    }
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    failed = [c["id"] for c in checks if c["result"] != "pass"]
    print(f"recon: {len(checks) - len(failed)}/{len(checks)} checks pass; anomalies missing={missing} unexpected={unexpected}")
    print(f"report: {args.out}")
    return 1 if failed or missing or unexpected else 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["deploy", "land", "run", "recon"])
    p.add_argument("--ns", default="cnvingest")
    p.add_argument("--source-dir")
    p.add_argument("--plant-anomalies", action="store_true")
    p.add_argument("--golden")
    p.add_argument("--out")
    p.add_argument("--run-mode", default="live", choices=["live", "fixture"])
    args = p.parse_args()
    require_ns(args.ns)
    dbx = Databricks()
    if args.command == "deploy":
        return cmd_deploy(dbx, args)
    if args.command == "land":
        if not args.source_dir:
            raise SystemExit("--source-dir required for land")
        return cmd_land(dbx, args)
    if args.command == "run":
        return cmd_run(dbx, args)
    if not (args.golden and args.out and args.source_dir):
        raise SystemExit("--golden, --source-dir and --out required for recon")
    return cmd_recon(dbx, args)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Reconcile the converted dbx-parse job against the golden legacy output.

The baseline is the .psv the legacy bash/sed/cut/awk parser actually produced in
this namespace's own deterministic legacy run — never a number copied out of a
document, never the converted job compared against itself. Every value on the
target side is recomputed by querying Unity Catalog after the job ran.

Phases (each one triggers the real Databricks job):
  1 golden      land + run over the namespace's golden drops, compare to the .psv
  2 idempotency rerun over the same input, compare contents before/after
  3 anomalies   land three anomaly drops (letter sed into an amount field, a
                non-existent calendar date, a trailer count that disagrees), run,
                and compare what the legacy parser does with the same bytes
  4 empty       run over an emptied landing slice; prior output must survive

Writes a recon report validated by `make tp-validate-recon`.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parse_sql as S
from client import Databricks, require_ident, require_ns
from custbill_layout import parse_file

REPO = Path(__file__).resolve().parents[2]
LEGACY_PARSER = REPO / "etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh"
PLANTED = ["non_numeric_amount", "invalid_calendar_date", "trailer_count_mismatch"]
CORRUPT_CUST = None  # discovered from the corrupted line itself
LEGACY_ROOT: Path | None = None  # set from --legacy-root before any unit() call


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def golden_rows(parsed_dir: Path) -> list:
    """Six-field rows exactly as the legacy parser wrote them, amounts converted
    to integer cents by digit surgery (no float ever touches the comparison)."""
    rows = []
    for psv in sorted(parsed_dir.glob("*.psv")):
        source_file = psv.stem + ".dat"
        for line in psv.read_text(encoding="latin-1").splitlines():
            if not line:
                continue
            cust_id, name, date, amount, currency, record_type = line.split("|")
            whole, _, frac = amount.partition(".")
            cents = int(whole) * 100 + int((frac + "00")[:2])
            rows.append((source_file, cust_id, name, date, cents, currency, record_type))
    return sorted(rows)


def golden_totals(rows: list) -> dict:
    totals = {}
    for _, _, _, _, cents, currency, record_type in rows:
        key = f"{currency},{'INVOICE' if record_type == '01' else 'CREDIT'}"
        count, total = totals.get(key, (0, 0))
        totals[key] = (count + 1, total + cents)
    return {key: list(value) for key, value in sorted(totals.items())}


def target_rows(dbx: Databricks, n: S.Names, files: set) -> list:
    rows = []
    for row in dbx.sql_ok(S.silver_rows(n)).rows:
        source_file, _source_line, cust_id, cust_name, bill_date, cents, currency, record_type = row
        if files and source_file not in files:
            continue
        rows.append((source_file, cust_id, cust_name, bill_date, int(cents), currency, record_type))
    return sorted(rows)


def target_totals(dbx: Databricks, n: S.Names, files: set) -> dict:
    """Recomputed from the target rows themselves so the rollup and the row-level
    comparison cannot disagree about which files are in scope."""
    totals = {}
    for source_file, _, _, _, cents, currency, record_type in target_rows(dbx, n, files):
        key = f"{currency},{'INVOICE' if record_type == '01' else 'CREDIT'}"
        count, total = totals.get(key, (0, 0))
        totals[key] = (count + 1, total + cents)
    return {key: list(value) for key, value in sorted(totals.items())}


def quarantine(dbx: Databricks, n: S.Names) -> list:
    return [
        {"source_file": row[0], "source_line": int(row[1]), "reason_code": row[2],
         "detail": row[3], "raw_bytes_base64": row[4]}
        for row in dbx.sql_ok(S.quarantine_rows(n)).rows
    ]


def content_hash(rows: list) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def unit(command: str, ns: str, extra: list = ()) -> str:
    """Drive parse_unit.py under the legacy root this recon was pointed at, so the
    landed drops and the golden baseline can never come from different directories."""
    argv = [sys.executable, str(Path(__file__).resolve().parent / "parse_unit.py"),
            command, "--ns", ns, *extra]
    env = dict(os.environ)
    if LEGACY_ROOT is not None:
        env["OTTERWORKS_LEGACY_ROOT"] = str(LEGACY_ROOT)
    completed = subprocess.run(argv, capture_output=True, text=True, cwd=str(REPO),
                               env=env, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"{command} failed: {completed.stdout}\n{completed.stderr}")
    return completed.stdout


def run_job(ns: str) -> dict:
    output = unit("run", ns)
    payload = json.loads(output[output.index("{"):])
    payload["notebook_summary"] = json.loads(payload["output"]) if payload.get("output") else {}
    return payload


def build_anomalies(source_dir: Path, out_dir: Path) -> dict:
    """Three fresh drops derived from this namespace's own golden bytes.

    The amount corruption is the demo beat: a single letter sed into the
    fixed-width amount field, which the legacy parser converts to 0.00 and passes.
    """
    drops = sorted(source_dir.glob("CUSTBILL*.dat"))
    if not drops:
        drops = sorted(source_dir.glob("CUSTBILL*.dat.done"))
    if len(drops) < 2:
        raise SystemExit(f"expected at least two CUSTBILL drops under {source_dir}")

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    first, second = drops[:2]

    corrupt = out_dir / "CUSTBILL_CNVPARSE_CORRUPT.dat"
    with corrupt.open("wb") as handle:
        subprocess.run(["sed", "2s/^\\(.\\{52\\}\\)./\\1A/", str(first)],
                       check=True, stdout=handle)

    baddate = out_dir / "CUSTBILL_CNVPARSE_BADDATE.dat"
    with baddate.open("wb") as handle:
        subprocess.run(["sed", "2s/^\\(.\\{40\\}\\).\\{8\\}/\\120230231/", str(second)],
                       check=True, stdout=handle)

    trailer = out_dir / "CUSTBILL_CNVPARSE_TRLMISMATCH.dat"
    with trailer.open("wb") as handle:
        subprocess.run(["sed", "3d", str(first)], check=True, stdout=handle)

    corrupted_line = corrupt.read_bytes().split(b"\n")[1]
    return {
        "corrupt": corrupt.name,
        "baddate": baddate.name,
        "trailer": trailer.name,
        "corrupt_cust_id": corrupted_line[:10].decode("latin-1").rstrip(),
        "corrupt_line_base64": base64.b64encode(corrupted_line).decode(),
    }


def legacy_on(path: Path, root: Path) -> list:
    """Run the untouched legacy parser over one drop in a throwaway root so the
    golden baseline is never disturbed."""
    if root.exists():
        shutil.rmtree(root)
    (root / "incoming").mkdir(parents=True)
    shutil.copy(path, root / "incoming" / path.name)
    env = dict(os.environ, OTTERWORKS_LEGACY_ROOT=str(root))
    subprocess.run(["bash", str(LEGACY_PARSER)], check=True, env=env,
                   capture_output=True, text=True)
    psv = root / "parsed" / (path.stem + ".psv")
    return psv.read_text(encoding="latin-1").splitlines()


def check(checks: list, cid: str, expected, actual, source: str) -> bool:
    passed = expected == actual
    checks.append({"id": cid, "expected": expected, "actual": actual,
                   "source_of_truth": source, "result": "pass" if passed else "fail"})
    return passed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ns", default="cnvparse")
    parser.add_argument("--catalog", default="ow_tp")
    parser.add_argument("--legacy-root", default=os.environ.get("OTTERWORKS_LEGACY_ROOT", "/tmp/otterworks-legacy"))
    parser.add_argument("--out", default="docs/tech-partnerships/recon/parse-cnvparse.recon.json")
    args = parser.parse_args(argv)

    ns = require_ns(args.ns)
    n = S.Names(catalog=require_ident(args.catalog, "catalog"), ns=ns)
    dbx = Databricks()
    legacy_root = Path(args.legacy_root)
    global LEGACY_ROOT
    LEGACY_ROOT = legacy_root
    golden = golden_rows(legacy_root / "parsed")
    golden_files = {row[0] for row in golden}
    checks: list = []
    evidence: dict = {}

    # ---- phase 1: golden drops -------------------------------------------
    unit("land", ns)
    evidence["golden_run"] = run_job(ns)["notebook_summary"]
    target = target_rows(dbx, n, golden_files)
    check(checks, "parse-row-parity",
          {"rows": len(golden), "files": sorted(golden_files)},
          {"rows": len(target), "files": sorted({row[0] for row in target})},
          f"legacy {legacy_root}/parsed/*.psv vs SELECT from {n.silver}")
    checks.append({
        "id": "parse-row-parity-fields",
        "expected": {"rows_only_in_legacy": 0, "rows_only_in_target": 0},
        "actual": {"rows_only_in_legacy": len([r for r in golden if r not in target]),
                   "rows_only_in_target": len([r for r in target if r not in golden])},
        "source_of_truth": "six-field row-by-row set comparison, legacy .psv vs silver",
        "result": "pass" if golden == target else "fail",
    })

    column_types = {row[0]: row[1].lower()
                    for row in dbx.sql_ok(f"DESCRIBE TABLE {n.silver}").rows if row[0]}
    check(checks, "parse-amount-exactness",
          {"totals_cents": golden_totals(golden), "amount_column_type": "bigint"},
          {"totals_cents": target_totals(dbx, n, golden_files),
           "amount_column_type": column_types.get("amount_cents", "?")},
          f"legacy .psv amounts as integer cents vs {n.silver}.amount_cents (no float in either path)")

    # ---- phase 2: idempotency --------------------------------------------
    before = (target_rows(dbx, n, set()), quarantine(dbx, n))
    evidence["idempotency_run"] = run_job(ns)["notebook_summary"]
    after = (target_rows(dbx, n, set()), quarantine(dbx, n))
    idempotent = content_hash(before) == content_hash(after)
    check(checks, "parse-idempotency",
          {"silver_hash": content_hash(before[0]), "quarantine_hash": content_hash(before[1]),
           "silver_rows": len(before[0]), "quarantine_rows": len(before[1])},
          {"silver_hash": content_hash(after[0]), "quarantine_hash": content_hash(after[1]),
           "silver_rows": len(after[0]), "quarantine_rows": len(after[1])},
          "job rerun over the same landed bytes; both sides recomputed from the target")

    # ---- phase 3: anomaly drops ------------------------------------------
    anomaly_dir = Path("/tmp") / f"otterworks-anomaly-{ns}"
    anomalies = build_anomalies(legacy_root / "incoming", anomaly_dir)
    evidence["anomaly_drops"] = anomalies
    unit("land", ns, ["--source", str(anomaly_dir)])
    evidence["anomaly_run"] = run_job(ns)["notebook_summary"]

    quarantined = quarantine(dbx, n)
    all_target = target_rows(dbx, n, set())
    per_file = {}
    for name in (anomalies["corrupt"], anomalies["baddate"], anomalies["trailer"]):
        parsed = parse_file(name, (anomaly_dir / name).read_bytes())
        per_file[name] = {
            "silver": len([r for r in all_target if r[0] == name]),
            "quarantine": [q for q in quarantined if q["source_file"] == name],
            "body_lines": parsed.body_count,
        }

    corrupt = per_file[anomalies["corrupt"]]
    legacy_corrupt = legacy_on(anomaly_dir / anomalies["corrupt"], Path("/tmp") / f"otterworks-legacy-corrupt-{ns}")
    legacy_corrupt_row = next((line for line in legacy_corrupt
                               if line.startswith(anomalies["corrupt_cust_id"] + "|")), "")
    check(checks, "parse-live-corruption",
          {"legacy_accepted_rows": 50, "legacy_row_for_corrupted_customer": "present with amount 0.00",
           "target_silver_rows": 49,
           "target_quarantine": [{"reason_code": "non_numeric_amount", "source_line": 2}]},
          {"legacy_accepted_rows": len(legacy_corrupt),
           "legacy_row_for_corrupted_customer": ("present with amount 0.00"
                                                 if legacy_corrupt_row.split("|")[3:4] == ["0.00"]
                                                 else f"unexpected: {legacy_corrupt_row}"),
           "target_silver_rows": corrupt["silver"],
           "target_quarantine": [{"reason_code": q["reason_code"], "source_line": q["source_line"]}
                                 for q in corrupt["quarantine"]]},
          "untouched legacy parser over the same corrupted drop vs silver/quarantine in the target")
    evidence["legacy_corrupt_row"] = legacy_corrupt_row

    baddate = per_file[anomalies["baddate"]]
    check(checks, "parse-date-typing",
          {"bill_date_column_type": "date", "silver_rows": 49,
           "quarantine_reasons": ["invalid_calendar_date"]},
          {"bill_date_column_type": column_types.get("bill_date", "?"),
           "silver_rows": baddate["silver"],
           "quarantine_reasons": sorted({q["reason_code"] for q in baddate["quarantine"]})},
          f"{n.silver} column type from DESCRIBE TABLE plus the 20230231 drop")

    trailer = per_file[anomalies["trailer"]]
    trailer_reasons = sorted({q["reason_code"] for q in trailer["quarantine"]})
    trailer_detail = next((q["detail"] for q in trailer["quarantine"]
                           if q["reason_code"] == "trailer_count_mismatch"), "")
    check(checks, "parse-trailer-reconciliation",
          {"silver_rows": 0, "reasons": ["file_failed_trailer_mismatch", "trailer_count_mismatch"],
           "mismatch_detail": "trailer=50 records=49", "quarantine_rows": 50},
          {"silver_rows": trailer["silver"], "reasons": trailer_reasons,
           "mismatch_detail": trailer_detail, "quarantine_rows": len(trailer["quarantine"])},
          f"trailer-vs-record counts recomputed from {n.quarantine}")

    accounting = {}
    for name, data in per_file.items():
        rows = data["quarantine"]
        # a whole-file rejection also records the TRL line itself (source_line > 0,
        # reason trailer_count_mismatch), so account for it separately
        file_level = len([q for q in rows if q["reason_code"] in ("trailer_count_mismatch", "missing_trailer")])
        accounting[name] = {
            "body_lines": data["body_lines"],
            "accounted": data["silver"] + len(rows) - file_level,
            "unattributed_rows": len([q for q in rows if not q["source_file"] or not q["reason_code"]
                                      or not q["raw_bytes_base64"] and q["source_line"] > 0]),
        }
    check(checks, "parse-quarantine-completeness",
          {name: {"body_lines": data["body_lines"], "accounted": data["body_lines"], "unattributed_rows": 0}
           for name, data in accounting.items()},
          accounting,
          f"every body line of each anomaly drop appears in {n.silver} or {n.quarantine}")

    # ---- phase 4: empty landing slice ------------------------------------
    before_empty = (target_rows(dbx, n, set()), quarantine(dbx, n))
    for item in dbx.list_dir(n.incoming):
        dbx.delete_file(item["path"])
    empty_run = run_job(ns)
    after_empty = (target_rows(dbx, n, set()), quarantine(dbx, n))
    check(checks, "parse-empty-input",
          {"run_result": "SUCCESS", "records_written": 0,
           "silver_rows": len(before_empty[0]), "quarantine_rows": len(before_empty[1])},
          {"run_result": empty_run["result_state"],
           "records_written": empty_run["notebook_summary"].get("records"),
           "silver_rows": len(after_empty[0]), "quarantine_rows": len(after_empty[1])},
          "job run over an emptied landing slice; prior output recounted from the target")
    evidence["empty_run"] = empty_run["notebook_summary"]
    # re-land the evidence so the namespace stays browsable after the recon
    unit("land", ns)
    unit("land", ns, ["--source", str(anomaly_dir)])

    final_quarantine = quarantine(dbx, n)
    observed = sorted({q["reason_code"] for q in final_quarantine})
    detected = [anomaly for anomaly in PLANTED if anomaly in observed]
    report = {
        "kind": "recon-report",
        "unit": "dbx-parse",
        "namespace": ns,
        "generated_at": now(),
        "run_mode": "live",
        "source_artifact": "etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh",
        "contract": "docs/tech-partnerships/contracts/parse_custbill_fixedwidth.json",
        "baseline_provenance": {
            "method": (f"make legacy-etl-gen-data NS={ns} then TP_FAKETIME='2026-01-15 00:00:00' "
                       f"scripts/tp-run-deterministic.sh make legacy-etl-run JOB=run_all NS={ns}"),
            "golden_output": f"{legacy_root}/parsed/*.psv",
            "golden_rows": len(golden),
            "golden_totals_cents": golden_totals(golden),
        },
        "target": {
            "silver": n.silver, "quarantine": n.quarantine, "job": n.job,
            "landing": n.incoming, "notebook": n.notebook,
        },
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if idempotent else "fail",
            "evidence": (
                f"job rerun over identical landed bytes; silver hash {content_hash(before[0])[:16]} -> "
                f"{content_hash(after[0])[:16]}, quarantine hash {content_hash(before[1])[:16]} -> "
                f"{content_hash(after[1])[:16]}"
            ),
        },
        "planted_anomaly_detections": {
            "expected_set": PLANTED,
            "actual_set": detected,
            "missing": [anomaly for anomaly in PLANTED if anomaly not in detected],
            "unexpected": [reason for reason in observed
                           if reason not in PLANTED + ["file_failed_trailer_mismatch"]],
        },
        "quarantine_reason_counts": {
            reason: len([q for q in final_quarantine if q["reason_code"] == reason])
            for reason in observed
        },
        "evidence": evidence,
        "unverified_paths": [
            (
                "encoding_invalid_byte, bad_record_length and missing_cust_id/missing_cust_name "
                "rejection paths are covered only by offline tests (tests/tp/test_custbill_layout.py); "
                "no live drop carrying those defects was landed in Databricks"
            ),
            (
                "legacy-side behaviour was captured live only for the corrupted-amount drop; the "
                "invalid-calendar-date and trailer-mismatch drops were not replayed through the legacy "
                "parser, so their legacy output is not part of this comparison"
            ),
            (
                "infrastructure/terraform-databricks/jobs_parse.tf is fmt-checked only; the shared "
                "Databricks stack is parent-owned and no terraform plan/apply was run from this unit"
            ),
            (
                "concurrent writers to the same landing slice and partially-written drops other than "
                "the .ok-marker case were not exercised"
            ),
            (
                "silver/quarantine writes now bind every value through named parameter markers, so no "
                "adversarial-content drop was needed to exercise a string-escaping path; the earlier "
                "esc() literal path silently dropped single quotes from cust_name/raw_line/detail "
                "(Spark reads '' inside a literal as adjacent strings, not an escaped quote) — the raw "
                "bytes were never affected because they are stored base64, but text columns written "
                "before this change are not byte-faithful"
            ),
        ],
        "recon_result": "pass" if all(c["result"] == "pass" for c in checks) and idempotent else "fail",
    }
    out = REPO / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    failures = [c["id"] for c in checks if c["result"] != "pass"]
    print(json.dumps({"out": str(out), "recon_result": report["recon_result"], "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

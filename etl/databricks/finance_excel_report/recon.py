#!/usr/bin/env python3
"""Fixture recon for the finance_excel_report migration unit.

Runs the pure-Python aggregation/rendering core (the exact code the
Databricks notebook executes) against silver-equivalent records produced by
the merged parse_custbill_fixedwidth unit's parsing core (consumed as-is,
never redefined) from the deterministic legacy demo inputs, and compares the
result against the immutable golden baseline recorded in the unit contract.
This is run_mode=fixture: it honestly covers cent-exact aggregation parity,
byte-exact CSV rendering, deterministic export naming, rescue exclusion,
NULL-attribution failure, empty-input semantics and rerun idempotency — it
does NOT execute SQL, Delta writes, UC or warehouse paths (listed as
unverified_paths; the parent proves those live after merge).

Usage: python3 etl/databricks/finance_excel_report/recon.py
Writes: docs/tech-partnerships/recon/finance_excel_report.recon.json
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
UNIT = "finance_excel_report"
NS = "demo"
STAMP = "20260115"  # TP_FAKETIME=2026-01-15 in the golden baseline run

# Golden baseline from docs/tech-partnerships/contracts/finance_excel_report.json
# (parent-recorded deterministic legacy output, NS=demo, TP_FAKETIME=2026-01-15).
GOLDEN_CSV_SHA256 = "c8923a71ab5a2d8048ad06ae91840631c009551e9082755fa4672e034a15627e"
GOLDEN_ROWS = [
    ("EUR", "INVOICE", 22, Decimal("101554.41")),
    ("EUR", "CREDIT", 6, Decimal("33375.97")),
    ("GBP", "INVOICE", 32, Decimal("183113.58")),
    ("GBP", "CREDIT", 5, Decimal("28454.59")),
    ("USD", "INVOICE", 28, Decimal("130502.15")),
    ("USD", "CREDIT", 7, Decimal("33390.44")),
]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def generate_demo_inputs(root: Path) -> dict[str, bytes]:
    env = dict(os.environ, OTTERWORKS_LEGACY_ROOT=str(root), TZ="UTC",
               LC_ALL="C", LANG="C")
    subprocess.run(
        ["perl", str(REPO_ROOT / "etl/legacy-extra/tools/gen_sample_data.pl"), NS],
        check=True, env=env, cwd=REPO_ROOT, stdout=subprocess.DEVNULL,
    )
    drop = root / "sftp-drop" / "upload"
    return {p.name: p.read_bytes() for p in sorted(drop.glob("CUSTBILL_*.dat"))}


def main() -> int:
    report_mod = load_module(
        Path(__file__).resolve().parent / "finance_excel_report.py",
        "finance_report",
    )
    # Consume the merged Wave 1 unit's parsing core as-is to produce the
    # silver-equivalent rows this gold job reads.
    parser = load_module(
        REPO_ROOT / "etl/databricks/parse_custbill_fixedwidth/parse_custbill_fixedwidth.py",
        "custbill_parser",
    )
    checks = []

    with tempfile.TemporaryDirectory(prefix="ow-tp-recon-") as tmp:
        inputs = generate_demo_inputs(Path(tmp))

    results = {name: parser.parse_file(name, data) for name, data in inputs.items()}
    silver_records = [r for res in results.values() for r in res.records]
    rescue_rows = [x for res in results.values() for x in res.rescues]
    silver_rows = [(r.currency, r.record_type, r.amount) for r in silver_records]

    # --- cent-parity: aggregate the silver-equivalent rows and compare the
    # six (currency, record_type, record_count, total_amount) rows exactly to
    # the cent against the contract golden.
    summary = report_mod.aggregate(silver_rows)
    actual_rows = [
        (r.currency, r.record_type, r.record_count, r.total_amount) for r in summary
    ]
    cent_parity = actual_rows == GOLDEN_ROWS
    checks.append({
        "id": "cent-parity",
        "expected": [[c, t, n, str(a)] for c, t, n, a in GOLDEN_ROWS],
        "actual": [[c, t, n, str(a)] for c, t, n, a in actual_rows],
        "source_of_truth": "contract golden aggregate rows (parent-recorded deterministic legacy run, NS=demo, TP_FAKETIME=2026-01-15)",
        "result": "pass" if cent_parity else "fail",
    })

    # --- real-artifact: render the CSV exactly as the notebook does, write
    # it to the fixture exports path under the volume layout with the
    # deterministic name, read it back and compare bytes + sha256 to the
    # golden CSV. UTF-8, LF, no BOM, no .xls masquerade anywhere.
    csv_bytes = report_mod.render_csv(summary)
    exports = (REPO_ROOT / ".tp-preflight" / "databricks-fixture" / "landing"
               / NS / UNIT / "exports")
    exports.mkdir(parents=True, exist_ok=True)
    export_name = report_mod.export_file_name(STAMP)
    export_path = exports / export_name
    export_path.write_bytes(csv_bytes)
    written = export_path.read_bytes()
    sha = hashlib.sha256(written).hexdigest()
    no_xls = not list(exports.glob("*.xls"))
    real_artifact = (
        written == csv_bytes
        and sha == GOLDEN_CSV_SHA256
        and export_name == f"finance_billing_{STAMP}.csv"
        and not written.startswith(b"\xef\xbb\xbf")
        and b"\r" not in written
        and no_xls
    )
    checks.append({
        "id": "real-artifact",
        "expected": {"name": f"finance_billing_{STAMP}.csv",
                     "sha256": GOLDEN_CSV_SHA256,
                     "encoding": "UTF-8, LF, no BOM, no .xls copy"},
        "actual": {"name": export_name, "sha256": sha, "bytes": len(written),
                   "bom": written.startswith(b"\xef\xbb\xbf"),
                   "crlf": b"\r" in written, "xls_files": not no_xls},
        "source_of_truth": "contract golden CSV sha256 vs bytes read back from the fixture exports path",
        "result": "pass" if real_artifact else "fail",
    })

    # --- rescue-excluded + quarantine-exclusion planted anomaly: synthesize
    # invalid records upstream (as the parent plants live), quarantine them
    # through the sibling parser, and prove the gold totals are unperturbed
    # to the cent while totals + rescue attribution reconcile with silver
    # row counts.
    def line(cust, name_, d, amt, ccy, rt):
        return f"{cust:<10.10}{name_:<30.30}{d:<8.8}{amt:<12.12}{ccy:<3.3}{rt:<2.2}".encode("ascii")

    bad_lines = [
        line("C000009901", "BAD DATE LTD", "20261332", "000000010000", "USD", "01"),
        line("C000009902", "BAD AMT GMBH", "20260116", "0000ABC00000", "EUR", "02"),
    ]
    anom = b"HDR CUSTBILL EXTRACT ANOM" + b" " * 40 + b"\n"
    anom += b"".join(ln + b"\n" for ln in bad_lines)
    anom += b"TRL" + b"0000000002" + b" " * 52 + b"\n"
    anom_result = parser.parse_file("CUSTBILL_DEMO_ANOM.dat", anom)

    all_records = silver_records + anom_result.records
    all_rescues = rescue_rows + anom_result.rescues
    planted_summary = report_mod.aggregate(
        [(r.currency, r.record_type, r.amount) for r in all_records]
    )
    planted_rows = [
        (r.currency, r.record_type, r.record_count, r.total_amount)
        for r in planted_summary
    ]
    body_total = sum(res.body_count for res in results.values()) + anom_result.body_count
    reconciles = (
        sum(r.record_count for r in planted_summary) + len(all_rescues) == body_total
    )
    quarantine_excluded = (
        len(anom_result.records) == 0
        and len(anom_result.rescues) == 2
        and planted_rows == GOLDEN_ROWS
        and reconciles
    )
    detected = ["quarantine-exclusion"] if quarantine_excluded else []
    checks.append({
        "id": "rescue-excluded",
        "expected": "planted invalid records quarantined to rescue; gold totals equal golden clean totals to the cent; counts + rescue attribution reconcile with silver body rows",
        "actual": {"planted_records_loaded": len(anom_result.records),
                   "planted_rescues": len(anom_result.rescues),
                   "totals_equal_golden": planted_rows == GOLDEN_ROWS,
                   "reconciles": reconciles},
        "source_of_truth": "synthesized upstream anomaly per contract planted_anomalies, quarantined by the merged parse unit's core (fixture)",
        "result": "pass" if quarantine_excluded else "fail",
    })

    # --- null-attribution: silver rows with NULL currency/record_type/amount
    # must fail the run with attribution, never aggregate.
    breaches = report_mod.find_attribution_breaches(
        silver_rows + [(None, "01", Decimal("1.00")), ("USD", None, Decimal("1.00")),
                       ("USD", "01", None)]
    )
    null_fails = breaches == [len(silver_rows) + 1, len(silver_rows) + 2,
                              len(silver_rows) + 3]
    checks.append({
        "id": "null-attribution-fails-run",
        "expected": "every row with NULL currency, record_type, or amount is attributed as a breach (the driver raises before aggregating)",
        "actual": {"breach_indices": breaches[-3:], "clean_rows_flagged": breaches[:-3]},
        "source_of_truth": "find_attribution_breaches (production breach detection) over clean rows plus three NULL-attributed rows (fixture)",
        "result": "pass" if null_fails else "fail",
    })

    # --- empty-input: zero silver rows produce a header-only export and zero
    # gold rows for the run key; other run keys untouched.
    empty_summary = report_mod.aggregate([])
    empty_csv = report_mod.render_csv(empty_summary)
    state: dict = {}
    report_mod.apply_to_state(state, NS, STAMP, summary)
    report_mod.apply_to_state(state, NS, "20260116", empty_summary)
    empty_ok = (
        empty_summary == []
        and empty_csv == b"Currency,RecordType,RecordCount,TotalAmount\n"
        and state["finance_billing_summary"][(NS, "20260116")] == []
        and state["finance_billing_summary"][(NS, STAMP)] == summary
    )
    checks.append({
        "id": "empty-input-header-only",
        "expected": "empty input: header-only CSV bytes, zero gold rows for the run key, prior run key untouched",
        "actual": {"empty_gold_rows": len(empty_summary),
                   "empty_csv": empty_csv.decode("utf-8"),
                   "prior_key_untouched": state["finance_billing_summary"][(NS, STAMP)] == summary},
        "source_of_truth": "aggregate/render_csv over zero rows plus run-key state simulation (fixture)",
        "result": "pass" if empty_ok else "fail",
    })

    # --- idempotency: re-applying the same run key (delete-then-insert per
    # (ns, report_date), as the notebook does) yields identical state and a
    # byte-identical export.
    rerun_state: dict = {}
    report_mod.apply_to_state(rerun_state, NS, STAMP, report_mod.aggregate(silver_rows))
    report_mod.apply_to_state(rerun_state, NS, STAMP, report_mod.aggregate(silver_rows))
    export_path.write_bytes(report_mod.render_csv(report_mod.aggregate(silver_rows)))
    rerun_sha = hashlib.sha256(export_path.read_bytes()).hexdigest()
    idempotent = (
        rerun_state["finance_billing_summary"][(NS, STAMP)] == summary
        and len(rerun_state["finance_billing_summary"]) == 1
        and rerun_sha == GOLDEN_CSV_SHA256
    )
    checks.append({
        "id": "idempotent-rerun",
        "expected": "double-apply of the run key yields identical gold rows and a byte-identical export",
        "actual": {"gold_rows": len(rerun_state["finance_billing_summary"][(NS, STAMP)]),
                   "rerun_export_sha256": rerun_sha},
        "source_of_truth": "delete-then-insert run-key state simulation of the notebook write path (fixture)",
        "result": "pass" if idempotent else "fail",
    })

    # --- no-silent-delivery: the migrated source contains no sendmail pipe
    # and no hardcoded legacy recipients; delivery is the export manifest.
    source = (Path(__file__).resolve().parent / "finance_excel_report.py").read_text()
    clean_delivery = all(
        needle not in source
        for needle in ("/usr/sbin/sendmail", "@otterworks.dev", "MAILTO",
                       '".xls"', ".xls'")
    )
    checks.append({
        "id": "no-silent-delivery",
        "expected": "no sendmail invocation, no hardcoded recipients, no .xls masquerade in the migrated source; export manifest printed to the run output",
        "actual": {"source_clean": clean_delivery},
        "source_of_truth": "static scan of the notebook source (fixture); live run-output manifest is parent-verified",
        "result": "pass" if clean_delivery else "fail",
    })

    report = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": NS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_mode": "fixture",
        "checks": checks,
        "values_recomputed_from_target": False,
        "idempotency_rerun": {
            "performed": True,
            "result": "pass" if idempotent else "fail",
            "evidence": "run key (demo, 20260115) applied twice through the per-(ns,report_date) delete-then-insert path; gold rows identical and export bytes match the golden sha256",
        },
        "planted_anomaly_detections": {
            "expected_set": ["quarantine-exclusion"],
            "actual_set": sorted(detected),
            "missing": sorted({"quarantine-exclusion"} - set(detected)),
            "unexpected": sorted(set(detected) - {"quarantine-exclusion"}),
        },
        "coverage_gaps": [
            {
                "id": "email-delivery-verification",
                "reason": "No mail transport exists in the demo workspace; delivery is verified as a volume artifact plus job run record instead of an SMTP round-trip (per contract).",
            }
        ],
        "unverified_paths": [
            "SQL execution on the serverless warehouse (CREATE TABLE IF NOT EXISTS, DELETE, append writes)",
            "Delta table semantics and DECIMAL(14,2) schema enforcement in Unity Catalog",
            "reading live ow_tp.silver.custbill_records / custbill_rescue (fixture uses the merged parse unit's core on the deterministic legacy inputs)",
            "Files API volume write of the export to /Volumes/ow_tp/bronze/landing/demo/finance_excel_report/exports/ (fixture exports path stands in locally)",
            "Unity Catalog permissions and catalog/schema resolution",
            "serverless notebook-task job execution (job:ow_tp_finance_excel_report) and its run-output delivery record",
            "live planted anomalies (parent plants them upstream during live validation; fixture used synthesized equivalents)",
        ],
        "export_manifest": [
            {"file": export_name, "sha256": sha, "bytes": len(written)}
        ],
        "fixture_exports": str(exports.relative_to(REPO_ROOT)),
    }

    out = REPO_ROOT / "docs" / "tech-partnerships" / "recon" / f"{UNIT}.recon.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    failed = [c["id"] for c in checks if c["result"] != "pass"]
    print(f"recon report written: {out.relative_to(REPO_ROOT)}")
    for c in checks:
        print(f"  {c['result']:4s}  {c['id']}")
    if failed:
        print(f"FAILED checks: {failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

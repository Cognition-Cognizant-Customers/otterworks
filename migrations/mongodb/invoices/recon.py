#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo==4.10.1"]
# ///
"""Reconcile the mongo_invoices target against the golden seed manifest.

Everything is RECOMPUTED from the target MongoDB — counts, embedded line
totals, and the INVOICE_LINE checksum (ordered md5 over sorted
"<line_id>:<amount>\\n" pairs, matching the seeder's Checksum in
testdata/legacy/oracle_billing_seed.py) over embedded lines plus quarantined
orphans, so every source line is accounted for exactly once.

Modes:
  --emit-core   print the canonical core numbers (for idempotency-rerun diff)
  --out <path>  write the machine-readable *.recon.json report
                (schema: docs/tech-partnerships/contracts/schema/recon-report.schema.json)
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient

UNIT = "mongo_invoices"
SOURCE_TARGET = "oracle.OW_BILLING.INVOICE_LINE"


def recompute(client, ns: str) -> dict:
    invoices = client[f"ow_tp_mongodb_{ns}"]["invoices"]
    quarantine = client[f"ow_tp_mongodb_{ns}_quarantine"]["invoice_lines_quarantine"]

    pairs = []
    n_invoices = 0
    n_embedded = 0
    for doc in invoices.find({"ns": ns}, {"lines.line_id": 1, "lines.amount": 1}):
        n_invoices += 1
        for line in doc.get("lines", []):
            n_embedded += 1
            pairs.append((line["line_id"],
                          f"{line['amount'].to_decimal():.2f}"))

    quarantine_kinds = {}
    n_quarantined = 0
    for doc in quarantine.find({"ns": ns},
                               {"amount": 1, "quarantine.kind": 1}):
        n_quarantined += 1
        kind = doc.get("quarantine", {}).get("kind", "unknown")
        quarantine_kinds[kind] = quarantine_kinds.get(kind, 0) + 1
        amount = doc.get("amount")
        pairs.append((doc["_id"],
                      f"{amount.to_decimal():.2f}" if amount is not None
                      else "None"))

    h = hashlib.md5()
    for pk, amt in sorted(pairs):
        h.update(f"{pk}:{amt}\n".encode())
    return {
        "invoices": n_invoices,
        "embedded_lines": n_embedded,
        "quarantined_lines": n_quarantined,
        "quarantine_kinds": dict(sorted(quarantine_kinds.items())),
        "total_lines": n_embedded + n_quarantined,
        "line_checksum": h.hexdigest(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--mongo-uri",
                    default=os.environ.get("MONGODB_URI",
                                           "mongodb://localhost:27017"))
    ap.add_argument("--manifest")
    ap.add_argument("--run-mode", choices=["fixture", "live"],
                    default="fixture")
    ap.add_argument("--emit-core", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--idempotency-result", choices=["pass", "fail"])
    ap.add_argument("--idempotency-evidence", default="")
    args = ap.parse_args()
    ns = args.ns

    client = MongoClient(args.mongo_uri)
    core = recompute(client, ns)
    client.close()

    if args.emit_core:
        print(json.dumps(core, indent=2, sort_keys=True))
        return 0

    manifest_path = Path(args.manifest or
                         Path(__file__).resolve().parents[3]
                         / "testdata" / "legacy" / "manifests" / f"{ns}.json")
    manifest = json.loads(manifest_path.read_text())
    hdr = manifest["targets"]["oracle.OW_BILLING.INVOICE_HEADER"]
    lines = manifest["targets"][SOURCE_TARGET]
    orphans = [a for a in manifest["planted_anomalies"]
               if a["target"] == SOURCE_TARGET]
    n_expected_orphans = sum(a["count"] for a in orphans
                             if a["kind"] == "orphaned_rows")

    def check(cid, expected, actual, source_of_truth):
        return {"id": cid, "expected": expected, "actual": actual,
                "source_of_truth": source_of_truth,
                "result": "pass" if expected == actual else "fail"}

    checks = [
        check("invoices-count", hdr["rows"], core["invoices"],
              f"manifest {manifest_path.name} targets.oracle.OW_BILLING."
              "INVOICE_HEADER.rows; actual recomputed from target "
              f"ow_tp_mongodb_{ns}.invoices"),
        check("lines-embedded", lines["rows"] - n_expected_orphans,
              core["embedded_lines"],
              "manifest INVOICE_LINE.rows minus planted orphaned_rows; "
              "actual recomputed by unwinding embedded lines from the target"),
        check("lines-checksum", lines["checksum"], core["line_checksum"],
              "manifest INVOICE_LINE.checksum; actual recomputed from the "
              "target as ordered md5 of sorted (line_id, amount) pairs over "
              "embedded lines + quarantined orphans (every source line "
              "exactly once)"),
        check("orphans-quarantined", n_expected_orphans,
              core["quarantine_kinds"].get("orphaned_rows", 0),
              "manifest planted_anomalies orphaned_rows count; actual "
              f"recomputed from ow_tp_mongodb_{ns}_quarantine."
              "invoice_lines_quarantine"),
        check("all-lines-accounted-once", lines["rows"], core["total_lines"],
              "manifest INVOICE_LINE.rows; actual embedded + quarantined "
              "recomputed from the target"),
    ]

    expected_set = sorted(f"{a['kind']}:{a['target']}:count={a['count']}"
                          for a in orphans)
    actual_set = sorted(f"{kind}:{SOURCE_TARGET}:count={count}"
                        for kind, count in core["quarantine_kinds"].items())
    report = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": ns,
        "generated_at": datetime.now(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": True,
            "result": args.idempotency_result or "fail",
            "evidence": args.idempotency_evidence,
        },
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": sorted(set(expected_set) - set(actual_set)),
            "unexpected": sorted(set(actual_set) - set(expected_set)),
        },
        "unverified_paths": [
            "live Atlas run (run_mode=fixture only; parent owns the live "
            "validation window)",
            "invalid-UTF-8 byte quarantine path (unexercised: the seeded "
            "namespace contains no undecodable rows and the AL32UTF8 driver "
            "decode raises before any row-level quarantine could run)",
            "NULL-amount quarantine path (seeded namespace contains no NULL "
            "amounts; policy implemented but unexercised)",
        ],
    }
    out = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(out)
    print(out)
    failed = [c["id"] for c in checks if c["result"] != "pass"]
    if report["idempotency_rerun"]["result"] != "pass":
        failed.append("idempotency-rerun")
    if failed or report["planted_anomaly_detections"]["missing"] \
            or report["planted_anomaly_detections"]["unexpected"]:
        print(f"[recon] FAIL: {failed}", file=sys.stderr)
        return 1
    print("[recon] all checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())

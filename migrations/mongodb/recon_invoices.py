#!/usr/bin/env python3
"""Recon generator for the mongo_invoices unit.

Recomputes counts, the ordered PK+amount md5 checksum, and the anomaly set
FROM the target MongoDB collections (never from migration-time memory), diffs
them against the immutable golden baseline manifest
(testdata/legacy/manifests/<ns>.json), and emits a recon report valid against
docs/tech-partnerships/contracts/schema/recon-report.schema.json.

Idempotency is proven by an actual rerun: `--state-out` captures a state
fingerprint after the first migration run, and `--idempotency-state` compares
the recomputed fingerprint after the second run.
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson.decimal128 import Decimal128
from pymongo import MongoClient

import tp_common

UNIT = "mongo_invoices"


def dec_str(value) -> str:
    if isinstance(value, Decimal128):
        return tp_common.amount_str(value.to_decimal())
    return tp_common.amount_str(value)


def compute_state(client, ns: str) -> dict:
    invoices = client[tp_common.target_db_name(ns)]["invoices"]
    qcoll = client[tp_common.quarantine_db_name(ns)]["invoice_lines_quarantine"]

    n_invoices = 0
    pairs: list[tuple[str, str]] = []  # (line_id, amount) across embedded + quarantined
    n_embedded = 0
    for doc in invoices.find({"ns": ns}, {"lines._line_id": 1, "lines.amount": 1}):
        n_invoices += 1
        for line in doc.get("lines", []):
            pairs.append((line["_line_id"], dec_str(line.get("amount"))))
            n_embedded += 1

    quarantined = list(qcoll.find({"ns": ns, "unit": UNIT}))
    q_by_reason: dict[str, list[str]] = {}
    n_quarantined_lines = 0
    for q in quarantined:
        q_by_reason.setdefault(q["reason"], []).append(q["_id"])
        if q.get("record_type") == "invoice_header":
            continue  # header-level quarantine never contributes a line
        n_quarantined_lines += 1
        pairs.append((q["_id"], dec_str(q.get("line", {}).get("amount"))))

    line_ids = [pk for pk, _ in pairs]
    duplicates = len(line_ids) - len(set(line_ids))
    ck = tp_common.OrderedChecksum()
    for pk, amt in sorted(pairs):
        ck.add(pk, amt)

    orphan_ids = sorted(q_by_reason.get("orphaned_line", []))
    state = {
        "invoices": n_invoices,
        "embedded_lines": n_embedded,
        "quarantined_total": len(quarantined),
        "quarantined_line_records": n_quarantined_lines,
        "quarantined_by_reason": {k: len(v) for k, v in sorted(q_by_reason.items())},
        "orphaned_line_ids_md5": hashlib.md5(
            "\n".join(orphan_ids).encode()).hexdigest(),
        "lines_accounted": ck.count,
        "duplicate_line_ids": duplicates,
        "lines_checksum": ck.hexdigest(),
    }
    state["fingerprint"] = hashlib.md5(
        json.dumps(state, sort_keys=True).encode()).hexdigest()
    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    ap.add_argument("--out")
    ap.add_argument("--state-out", help="write the state fingerprint and exit")
    ap.add_argument("--idempotency-state",
                    help="prior --state-out file to compare against for the rerun proof; "
                         "required when emitting a report (the schema mandates a rerun)")
    args = ap.parse_args()
    ns = args.ns
    if not tp_common.valid_ns(ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    client = MongoClient(tp_common.mongo_uri(args.run_mode))
    state = compute_state(client, ns)
    client.close()

    if args.state_out:
        Path(args.state_out).write_text(json.dumps(state, indent=2) + "\n")
        print(f"[recon] state fingerprint written: {args.state_out}")
        return 0

    if not args.idempotency_state:
        print(json.dumps(state, indent=2))
        print("[recon] no --idempotency-state given: schema-valid reports require "
              "the rerun proof; state printed instead (use verify_invoices.sh "
              "for the full migrate -> rerun -> report flow)", file=sys.stderr)
        return 0

    manifest_file = tp_common.MANIFESTS_DIR / f"{ns}.json"
    manifest = json.loads(manifest_file.read_text())
    exp_headers = manifest["targets"]["oracle.OW_BILLING.INVOICE_HEADER"]["rows"]
    exp_lines = manifest["targets"]["oracle.OW_BILLING.INVOICE_LINE"]["rows"]
    exp_checksum = manifest["targets"]["oracle.OW_BILLING.INVOICE_LINE"]["checksum"]
    exp_orphans = next(a["count"] for a in manifest["planted_anomalies"]
                       if a["kind"] == "orphaned_rows"
                       and a["target"] == "oracle.OW_BILLING.INVOICE_LINE")

    src = f"manifest {manifest_file.relative_to(tp_common.REPO_ROOT)}"
    tgt = "recomputed from target MongoDB collections"
    orphan_count = state["quarantined_by_reason"].get("orphaned_line", 0)
    checks = [
        {"id": "invoices-count", "expected": exp_headers,
         "actual": state["invoices"],
         "source_of_truth": f"{src} (INVOICE_HEADER.rows) vs {tgt}",
         "result": "pass" if state["invoices"] == exp_headers else "fail"},
        {"id": "lines-embedded",
         "expected": exp_lines - state["quarantined_line_records"],
         "actual": state["embedded_lines"],
         "source_of_truth": f"{src} (INVOICE_LINE.rows minus all quarantined "
                            f"line records, recomputed from target) vs {tgt}",
         "result": "pass"
         if state["embedded_lines"] == exp_lines - state["quarantined_line_records"]
         else "fail"},
        {"id": "lines-checksum",
         "expected": {"checksum": exp_checksum, "rows": exp_lines},
         "actual": {"checksum": state["lines_checksum"],
                    "rows": state["lines_accounted"],
                    "duplicate_line_ids": state["duplicate_line_ids"]},
         "source_of_truth": f"{src} (INVOICE_LINE.checksum over embedded lines "
                            f"plus quarantined orphans) vs {tgt}",
         "result": "pass" if (state["lines_checksum"] == exp_checksum
                              and state["lines_accounted"] == exp_lines
                              and state["duplicate_line_ids"] == 0) else "fail"},
        {"id": "orphans-quarantined", "expected": exp_orphans,
         "actual": {"orphaned_line": orphan_count,
                    "quarantined_by_reason": state["quarantined_by_reason"],
                    "orphaned_line_ids_md5": state["orphaned_line_ids_md5"]},
         "source_of_truth": f"{src} (planted orphaned_rows count) vs {tgt} "
                            "(quarantine collection, attributed and enumerated)",
         "result": "pass" if orphan_count == exp_orphans else "fail"},
    ]

    prior = json.loads(Path(args.idempotency_state).read_text())
    same = prior["fingerprint"] == state["fingerprint"]
    idem = {"performed": True, "result": "pass" if same else "fail",
            "evidence": (f"migration rerun for ns={ns}: state fingerprint "
                         f"{prior['fingerprint']} (run 1) vs "
                         f"{state['fingerprint']} (run 2), "
                         f"{'identical' if same else 'DIFFERENT'} counts/"
                         "checksums/quarantine sets recomputed from the target")}

    expected_set = [f"orphaned_rows:{exp_orphans}"]
    actual_set = [f"orphaned_rows:{orphan_count}"] if orphan_count else []
    report = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": ns,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": idem,
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": sorted(set(expected_set) - set(actual_set)),
            "unexpected": sorted(set(actual_set) - set(expected_set)),
        },
        "unverified_paths": [
            "live Atlas run (this report is run_mode=fixture; the parent owns the live window)",
            "invalid_utf8 quarantine path (no non-UTF-8 rows exist in the seeded fixture)",
            "null_amount quarantine path (no NULL amounts exist in the seeded fixture)",
            "empty-input no-op verified only against an unseeded namespace, not a truncated one",
        ],
    }
    out = Path(args.out) if args.out else (
        tp_common.REPO_ROOT / "docs/tech-partnerships/recon"
        / f"{UNIT}.{ns}.recon.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    failed = [c["id"] for c in checks if c["result"] != "pass"]
    print(f"[recon] report written: {out}")
    print(f"[recon] checks: {len(checks) - len(failed)}/{len(checks)} pass"
          + (f" (FAILED: {', '.join(failed)})" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

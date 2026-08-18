#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pymongo==4.10.1"]
# ///
"""Reconcile the migrated customers collections against the golden manifest.

Every value is RECOMPUTED from the target MongoDB (never from migration-time
memory): document counts, the ordered PK+amount md5 checksum exactly as the
seeder recorded it into testdata/legacy/manifests/<ns>.json, the folded EAV
entry total, and the planted-anomaly detections grouped from the quarantine
collection. Emits a machine-readable report valid against
docs/tech-partnerships/contracts/schema/recon-report.schema.json.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from pymongo import MongoClient

UNIT = "mongo_customers"
ANOMALY_KINDS = {
    "malformed_csv_lists": "oracle.OW_BILLING.CUSTOMER_MASTER.RELATED_ACCT_IDS",
    "dirty_dates": "oracle.OW_BILLING.CUSTOMER_MASTER.SIGNUP_DT",
}


def recompute(client: MongoClient, ns: str) -> dict:
    customers = client[f"ow_tp_mongodb_{ns}"]["customers"]
    quarantine = client[f"ow_tp_mongodb_{ns}_quarantine"]["customers_quarantine"]

    n_customers = customers.count_documents({"ns": ns})

    ck = hashlib.md5()
    n_eav = 0
    n_array_csv = 0
    n_date_signup = 0
    for doc in customers.find({"ns": ns}, sort=[("_id", 1)]):
        bal = doc.get("balances", {}).get("current")
        bal_s = f"{bal:.2f}" if bal is not None else ""
        ck.update(f"{doc['_id']}:{bal_s}\n".encode())
        for entries in doc.get("attributes", {}).values():
            n_eav += len(entries)
        for field in ("related_acct_ids", "promo_codes"):
            if field in doc and isinstance(doc[field], list):
                n_array_csv += 1
        if "signup_dt" in doc and isinstance(doc["signup_dt"], datetime.datetime):
            n_date_signup += 1

    detections: dict[str, dict] = {}
    for group in quarantine.aggregate([
        {"$match": {"ns": ns}},
        {"$group": {"_id": {"kind": "$anomaly_kind", "field": "$field"},
                    "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]):
        kind = group["_id"]["kind"]
        field = group["_id"]["field"]
        planted_field = ANOMALY_KINDS.get(kind, "").rsplit(".", 1)[-1]
        key = kind if field == planted_field else f"{kind}[{field}]"
        entry = detections.setdefault(key, {"kind": kind, "count": 0, "fields": set()})
        entry["count"] += group["count"]
        entry["fields"].add(field)

    meta = client[f"ow_tp_mongodb_{ns}"]["customers_migration_meta"].find_one(
        {"_id": ns}) or {}
    src = meta.get("source_nonnull_counts", {})

    def quarantined(field: str) -> int:
        return quarantine.count_documents({"ns": ns, "field": field})

    expected_arrays = (
        src.get("RELATED_ACCT_IDS", 0) - quarantined("RELATED_ACCT_IDS")
        + src.get("PROMO_CODES_CSV", 0) - quarantined("PROMO_CODES_CSV"))
    expected_dates = src.get("SIGNUP_DT", 0) - quarantined("SIGNUP_DT")

    return {
        "customers": n_customers,
        "checksum": ck.hexdigest(),
        "eav_entries": n_eav,
        "array_csv": n_array_csv,
        "date_signup": n_date_signup,
        "expected_arrays": expected_arrays,
        "expected_dates": expected_dates,
        "detections": detections,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--mongodb-uri",
                    default=os.environ.get("MONGODB_URI", "mongodb://localhost:27017"))
    ap.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--idempotency-evidence", default=None,
                    help="Evidence string proving a rerun reproduced identical numbers; "
                         "required for a full report.")
    args = ap.parse_args()
    ns = args.ns
    if not re.fullmatch(r"[A-Za-z0-9_]+", ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[3]
    manifest_path = Path(args.manifest) if args.manifest else \
        root / "testdata" / "legacy" / "manifests" / f"{ns}.json"
    manifest = json.loads(manifest_path.read_text())
    m_cust = manifest["targets"]["oracle.OW_BILLING.CUSTOMER_MASTER"]
    m_eav = manifest["targets"]["oracle.OW_BILLING.ENTITY_ATTR_VALUE"]
    expected_anomalies = [a for a in manifest["planted_anomalies"]
                          if a["kind"] in ANOMALY_KINDS
                          and a["target"] == ANOMALY_KINDS[a["kind"]]]

    if not args.idempotency_evidence and args.out is None:
        print("[recon] refusing to write a schema-invalid report to the shared recon "
              "directory without --idempotency-evidence; pass --out for a scratch run",
              file=sys.stderr)
        return 2

    client = MongoClient(args.mongodb_uri)
    actual = recompute(client, ns)
    client.close()

    def check(cid: str, expected, actual_value, source: str) -> dict:
        return {"id": cid, "expected": expected, "actual": actual_value,
                "source_of_truth": source,
                "result": "pass" if expected == actual_value else "fail"}

    manifest_src = f"testdata/legacy/manifests/{ns}.json"
    checks = [
        check("customers-count", m_cust["rows"], actual["customers"], manifest_src),
        check("customers-checksum", m_cust["checksum"], actual["checksum"],
              f"{manifest_src} (ordered PK+CUR_BAL_AMT md5, recomputed from target)"),
        check("eav-folded", m_eav["rows"], actual["eav_entries"],
              f"{manifest_src} (sum of attributes entries across documents)"),
        check("csv-to-arrays", actual["expected_arrays"], actual["array_csv"],
              "source non-NULL RELATED_ACCT_IDS/PROMO_CODES_CSV rows (persisted by "
              "migrate.py at extract time) minus quarantined malformed lists, vs "
              "array-valued fields recomputed from target documents"),
        check("dates-to-bson", actual["expected_dates"], actual["date_signup"],
              "source non-NULL SIGNUP_DT rows (persisted by migrate.py at extract "
              "time) minus quarantined dirty dates, vs BSON-date signup_dt "
              "recomputed from target documents"),
    ]

    expected_set = sorted(
        f"{a['kind']}:{a['count']}" for a in expected_anomalies)
    actual_set = sorted(
        f"{key}:{entry['count']}" for key, entry in actual["detections"].items())
    missing = sorted(set(expected_set) - set(actual_set))
    unexpected = sorted(set(actual_set) - set(expected_set))
    if missing or unexpected:
        checks.append({"id": "planted-anomalies", "expected": expected_set,
                       "actual": actual_set, "source_of_truth": manifest_src,
                       "result": "fail"})

    report = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": ns,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": args.run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": {
            "performed": bool(args.idempotency_evidence),
            "result": "pass" if args.idempotency_evidence else "fail",
            **({"evidence": args.idempotency_evidence}
               if args.idempotency_evidence else {}),
        },
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": missing,
            "unexpected": unexpected,
        },
        "unverified_paths": [
            "live Atlas run (run_mode=live): this unit self-verifies against a local "
            "fixture only; the parent runs the single live validation window",
            "invalid-UTF-8 quarantine path (invalid_encoding): no invalid bytes are "
            "planted in the estate and the Oracle driver decodes AL32UTF8 before the "
            "migration sees values, so this guard is untested end-to-end",
        ],
    }

    out = Path(args.out) if args.out else \
        root / "docs" / "tech-partnerships" / "recon" / f"{UNIT}-{ns}.recon.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")

    failed = [c["id"] for c in checks if c["result"] != "pass"]
    failed += ["planted-anomalies"] if (missing or unexpected) else []
    print(f"[recon] ns={ns} report={out}")
    for c in checks:
        print(f"[recon] {c['id']}: {c['result']} (expected={c['expected']} actual={c['actual']})")
    print(f"[recon] anomalies expected={expected_set} actual={actual_set} "
          f"missing={missing} unexpected={unexpected}")
    if failed or not args.idempotency_evidence:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

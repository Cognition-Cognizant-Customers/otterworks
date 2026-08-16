#!/usr/bin/env python3
"""Recon generator for the mongo_customers unit.

Recomputes every value FROM the target MongoDB (never from migration-time
memory) and diffs against the immutable golden baseline manifest
(testdata/legacy/manifests/<ns>.json). Emits a recon report valid against
docs/tech-partnerships/contracts/schema/recon-report.schema.json.

Standalone usage proves idempotency itself (mirroring the mongo_files unit):
run once without --compare to write a baseline snapshot (intermediate, NOT
committable), rerun the migration, then run again with --compare <baseline>
to emit the schema-valid report with real rerun evidence.

Usage:
  python3 recon_customers.py --ns demo --out /tmp/baseline.json
  python3 migrate_customers.py --ns demo
  python3 recon_customers.py --ns demo --run-mode fixture \
      --compare /tmp/baseline.json --out <report.recon.json>
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

import mongo_common

UNIT = "mongo_customers"
CM_TARGET = "oracle.OW_BILLING.CUSTOMER_MASTER"
EAV_TARGET = "oracle.OW_BILLING.ENTITY_ATTR_VALUE"
ANOMALY_TARGETS = {
    "dirty_dates": "oracle.OW_BILLING.CUSTOMER_MASTER.SIGNUP_DT",
    "malformed_csv_lists": "oracle.OW_BILLING.CUSTOMER_MASTER.RELATED_ACCT_IDS",
}


def unverified_paths(run_mode: str) -> list:
    paths = [
        "invalid_utf8 quarantine path: the seeded fixture plants no non-UTF-8 "
        "bytes, so this path is implemented but not exercised by fixture data",
    ]
    if run_mode == "fixture":
        paths.append(
            "live Atlas transport: this report is run_mode=fixture; the "
            "parent session runs the live window")
    return paths


def compute(ns: str) -> dict:
    """Recompute all recon values from the target collections."""
    client = mongo_common.mongo_client()
    customers = client[mongo_common.target_db_name(ns)]["customers"]
    quarantine = client[mongo_common.quarantine_db_name(ns)]["customers_quarantine"]

    count = customers.count_documents({})
    pairs = []
    missing_checksum_inputs = 0
    attr_entries = 0
    array_typed_related = array_typed_promo = 0
    string_typed_csv_fields = 0
    bson_date_signup = 0
    for doc in customers.find(
            {}, {"cust_id": 1, "cur_bal_amt": 1, "attributes": 1,
                 "related_acct_ids": 1, "promo_codes_csv": 1, "signup_dt": 1}):
        if "cust_id" in doc and "cur_bal_amt" in doc:
            pairs.append((doc["cust_id"], f"{doc['cur_bal_amt']:.2f}"))
        else:
            # NULL source values are omitted fields; never fabricate a 0.00
            # balance into the checksum — count and surface instead.
            missing_checksum_inputs += 1
        attr_entries += len(doc.get("attributes", []))
        if isinstance(doc.get("related_acct_ids"), list):
            array_typed_related += 1
        if isinstance(doc.get("promo_codes_csv"), list):
            array_typed_promo += 1
        for csv_field in ("related_acct_ids", "promo_codes_csv"):
            if csv_field in doc and not isinstance(doc[csv_field], list):
                string_typed_csv_fields += 1
        if isinstance(doc.get("signup_dt"), datetime):
            bson_date_signup += 1
    checksum = mongo_common.ordered_pk_checksum(pairs)

    q_by_kind_field: dict[tuple, int] = {}
    for q in quarantine.find({"unit": UNIT}, {"kind": 1, "field": 1}):
        key = (q["kind"], q["field"])
        q_by_kind_field[key] = q_by_kind_field.get(key, 0) + 1
    client.close()
    return {
        "count": count,
        "checksum": checksum,
        "missing_checksum_inputs": missing_checksum_inputs,
        "attr_entries": attr_entries,
        "array_typed_related": array_typed_related,
        "array_typed_promo": array_typed_promo,
        "string_typed_csv_fields": string_typed_csv_fields,
        "bson_date_signup": bson_date_signup,
        "quarantine_by_kind_field": {
            f"{k}:{f}": n for (k, f), n in sorted(q_by_kind_field.items())
        },
    }


def build_report(ns: str, run_mode: str, actual: dict,
                 idempotency: dict) -> dict:
    manifest = mongo_common.load_manifest(ns)
    if not manifest or CM_TARGET not in manifest.get("targets", {}):
        raise SystemExit(
            f"[{UNIT}] golden baseline manifest testdata/legacy/manifests/"
            f"{ns}.json is missing or lacks {CM_TARGET}; seed the fixture "
            "first (make oracle-billing-seed NS=" + ns + ")")
    targets = manifest.get("targets", {})
    cm_rows = targets.get(CM_TARGET, {}).get("rows")
    cm_checksum = targets.get(CM_TARGET, {}).get("checksum")
    eav_rows = targets.get(EAV_TARGET, {}).get("rows")
    anomalies = {
        a["kind"]: a["count"]
        for a in manifest.get("planted_anomalies", [])
        if a.get("target") in ANOMALY_TARGETS.values()
    }
    q = actual["quarantine_by_kind_field"]
    dirty = q.get("dirty_dates:signup_dt", 0)
    badcsv = q.get("malformed_csv_lists:related_acct_ids", 0)

    def check(cid, expected, got, source):
        return {"id": cid, "expected": expected, "actual": got,
                "source_of_truth": source,
                "result": "pass" if expected == got else "fail"}

    manifest_src = f"testdata/legacy/manifests/{ns}.json"
    checks = [
        check("customers-count", cm_rows, actual["count"], manifest_src),
        check("customers-checksum", cm_checksum, actual["checksum"],
              manifest_src),
        check("eav-folded", eav_rows, actual["attr_entries"], manifest_src),
        check("checksum-inputs-present", 0, actual["missing_checksum_inputs"],
              "target collection scan (documents lacking cust_id/cur_bal_amt)"),
        check("csv-to-arrays",
              {"quarantined_malformed_csv": anomalies.get("malformed_csv_lists"),
               "valid_lists_are_arrays": True},
              {"quarantined_malformed_csv": badcsv,
               "valid_lists_are_arrays":
                   actual["string_typed_csv_fields"] == 0
                   and actual["array_typed_related"] > 0
                   and actual["array_typed_promo"] > 0},
              manifest_src + " + target collection type scan"),
        check("dates-to-bson",
              {"quarantined_dirty_dates": anomalies.get("dirty_dates"),
               "valid_dates_are_bson": cm_rows - anomalies.get("dirty_dates", 0)},
              {"quarantined_dirty_dates": dirty,
               "valid_dates_are_bson": actual["bson_date_signup"]},
              manifest_src + " + target collection type scan"),
    ]
    expected_set = sorted(f"{k}:{v}" for k, v in anomalies.items())
    # Aggregate everything actually quarantined, per kind across all fields,
    # so anomaly categories beyond the planted ones surface as "unexpected".
    actual_by_kind: dict = {}
    for kind_field, n in q.items():
        kind = kind_field.rsplit(":", 1)[0]
        actual_by_kind[kind] = actual_by_kind.get(kind, 0) + n
    actual_set = sorted(f"{k}:{v}" for k, v in actual_by_kind.items())
    return {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": ns,
        "generated_at": datetime.now(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_mode": run_mode,
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": idempotency,
        "planted_anomaly_detections": {
            "expected_set": expected_set,
            "actual_set": actual_set,
            "missing": sorted(set(expected_set) - set(actual_set)),
            "unexpected": sorted(set(actual_set) - set(expected_set)),
        },
        "unverified_paths": unverified_paths(run_mode),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    ap.add_argument("--out", required=True)
    ap.add_argument("--compare", help="baseline snapshot from a previous run; "
                    "required to emit a committable report with real rerun "
                    "evidence")
    args = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]+", args.ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    actual = compute(args.ns)
    if not args.compare:
        with open(args.out, "w") as f:
            json.dump(actual, f, indent=2)
            f.write("\n")
        print(f"[{UNIT}] baseline snapshot written to {args.out} "
              "(intermediate, NOT committable); rerun the migration and pass "
              "--compare to emit the report with rerun evidence")
        return 0
    with open(args.compare) as f:
        baseline = json.load(f)
    identical = baseline == actual
    idempotency = {
        "performed": True,
        "result": "pass" if identical else "fail",
        "evidence": ("recon values recomputed from the target after a rerun "
                     f"are {'identical to' if identical else 'DIFFERENT from'} "
                     f"the baseline snapshot {args.compare}"),
    }
    report = build_report(args.ns, args.run_mode, actual, idempotency)
    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    failed = [c["id"] for c in report["checks"] if c["result"] != "pass"]
    if not identical:
        failed.append("idempotency-rerun")
    print(f"[{UNIT}] recon written to {args.out}; "
          f"{'FAIL: ' + ','.join(failed) if failed else 'all checks pass'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

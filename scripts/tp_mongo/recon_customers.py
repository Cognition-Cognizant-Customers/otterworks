#!/usr/bin/env python3
"""Reconcile the ``mongo_customers`` migration by reading the target back.

Every number in the emitted report is recomputed by querying the target
database after the write — nothing is carried over from the migration's own
counters. The target is addressed only through ``MONGO_URI`` (default: the local
``mongo:7`` fixture), so the same command reconciles the local fixture and the
shared Atlas cluster.

With ``--rerun-migration`` the script fingerprints both collections, runs the
migration a second time, fingerprints again and compares, so the idempotency
claim in the report is an executed rerun rather than an assertion.

Usage:
    MONGO_URI=mongodb://localhost:27017 MONGO_DB=ow_tp_demo \\
      uv run --with pymongo==4.10.1 --with oracledb==2.5.1 \\
      scripts/tp_mongo/recon_customers.py --ns demo --run-mode fixture \\
      --rerun-migration --out docs/tech-partnerships/recon/mongo_customers.recon.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from bson.json_util import CANONICAL_JSON_OPTIONS, dumps
from pymongo import MongoClient
from pymongo.errors import WriteError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from customers_model import (  # noqa: E402
    ANOMALY_DIRTY_SIGNUP_DT,
    ANOMALY_MALFORMED_RELATED_ACCT_IDS,
    SCHEMA_VERSION,
    balance_checksum,
)
from migrate_customers import (  # noqa: E402
    CUSTOMERS,
    QUARANTINE,
    mongo_uri,
    target_db_name,
)

UNIT = "mongo_customers"
MANIFEST_CUSTOMERS = "oracle.OW_BILLING.CUSTOMER_MASTER"
MANIFEST_EAV = "oracle.OW_BILLING.ENTITY_ATTR_VALUE"
# Contract planted-anomaly ids mapped to the anomaly ids stamped on quarantine
# records by the migration.
PLANTED = {
    "dirty_dates": ANOMALY_DIRTY_SIGNUP_DT,
    "malformed_csv_lists": ANOMALY_MALFORMED_RELATED_ACCT_IDS,
}
# Representative sparse columns: they must be absent when the source is NULL,
# never present as an explicit null (contract ``null_attribution: fail``).
SPARSE_PATHS = ("dba_name", "terminate_date", "udf.text_01", "udf.amount_01",
                "udf.date_01", "contacts.fax", "mail_address.line1",
                "child_acct_ids", "attributes", "flags.legacy.flag_01")


def check(checks: list[dict[str, Any]], cid: str, expected: Any, actual: Any,
          source: str) -> None:
    checks.append({
        "id": cid,
        "expected": expected,
        "actual": actual,
        "source_of_truth": source,
        "result": "pass" if expected == actual else "fail",
    })


def fingerprint(db, ns: str) -> dict[str, str]:
    """md5 over every document of this namespace in canonical extended JSON."""
    out = {}
    for name in (CUSTOMERS, QUARANTINE):
        digest = hashlib.md5()
        for doc in db[name].find({"ns": ns}).sort("_id", 1):
            digest.update(dumps(doc, json_options=CANONICAL_JSON_OPTIONS,
                                sort_keys=True).encode())
            digest.update(b"\n")
        out[name] = digest.hexdigest()
    return out


def target_checksum(db, ns: str) -> str:
    """Recompute the manifest's CUSTOMER_MASTER checksum from the target."""
    pairs: list[tuple[str, str]] = []
    for doc in db[CUSTOMERS].find({"ns": ns}, {"balances.current": 1}):
        amount = doc.get("balances", {}).get("current")
        if amount is None:
            raise SystemExit(f"document {doc['_id']} has no balances.current")
        pairs.append((doc["_id"], f"{amount.to_decimal():.2f}"))
    return balance_checksum(pairs)


def count_typed(db, name: str, ns: str, path: str, bson_type: str) -> int:
    return db[name].count_documents({"ns": ns, path: {"$type": bson_type}})


def validator_rejects(db, ns: str) -> bool:
    """Prove the ``$jsonSchema`` validator rejects a bad document.

    Uses a document that fails on exactly the modeling the demo narrates: a
    ``SIGNUP_DT`` left as its dirty legacy string and a CSV list left as a
    string instead of an array. The insert must be refused, so nothing is
    written and the target needs no cleanup.
    """
    bad = {
        "_id": "00000000-0000-0000-0000-000000000000",
        "ns": ns,
        "schema_version": SCHEMA_VERSION,
        "cust_no": "RECON-VALIDATOR-PROBE",
        "name": "recon validator probe",
        "tenant_id": "recon",
        "signup_date": "31-FEB-24",
        "related_acct_ids": "12345,,67890,",
    }
    try:
        db[CUSTOMERS].insert_one(bad)
    except WriteError:
        return True
    db[CUSTOMERS].delete_one({"_id": bad["_id"]})
    return False


def anomaly_set(db, ns: str) -> list[str]:
    """Recompute detected planted anomalies from the quarantine collection."""
    out = []
    for contract_id, anomaly_id in sorted(PLANTED.items()):
        n = db[QUARANTINE].count_documents(
            {"ns": ns, "anomalies.anomaly_id": anomaly_id})
        out.append(f"{contract_id}:{n}")
    return out


def run_migration(ns: str) -> str:
    """Run the migration again in-process-free, returning its stdout summary."""
    script = Path(__file__).resolve().parent / "migrate_customers.py"
    proc = subprocess.run([sys.executable, str(script), "--ns", ns],
                          capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ns", required=True)
    p.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    p.add_argument("--manifest", default="testdata/legacy/manifests")
    p.add_argument("--rerun-migration", action="store_true",
                   help="run the migration a second time and compare target state")
    p.add_argument("--out", help="write the recon report here (default: stdout)")
    args = p.parse_args()

    manifest = json.loads(
        (Path(args.manifest) / f"{args.ns}.json").read_text())["targets"]
    expected_rows = manifest[MANIFEST_CUSTOMERS]["rows"]
    expected_checksum = manifest[MANIFEST_CUSTOMERS]["checksum"]
    expected_eav = manifest[MANIFEST_EAV]["rows"]

    client: MongoClient = MongoClient(mongo_uri(), tz_aware=False)
    db = client[target_db_name(args.ns)]
    checks: list[dict[str, Any]] = []

    check(checks, "customers.count", expected_rows,
          db[CUSTOMERS].count_documents({"ns": args.ns}),
          f"{db.name}.{CUSTOMERS} countDocuments after write")
    check(checks, "customers.checksum", expected_checksum,
          target_checksum(db, args.ns),
          f"md5 over (_id, balances.current) read back from {db.name}.{CUSTOMERS}")

    folded = next(db[CUSTOMERS].aggregate([
        {"$match": {"ns": args.ns, "attributes_entries": {"$exists": True}}},
        {"$project": {"n": {"$size": "$attributes_entries"},
                      "ids": "$attributes_entries.eav_id"}},
        {"$group": {"_id": None, "rows": {"$sum": "$n"},
                    "ids": {"$addToSet": "$ids"}}},
        {"$project": {"rows": 1,
                      "distinct": {"$size": {"$reduce": {
                          "input": "$ids", "initialValue": [],
                          "in": {"$setUnion": ["$$value", "$$this"]}}}}}},
    ]))
    check(checks, "customers.attributes_folded", expected_eav, folded["rows"],
          f"sum of attributes_entries sizes in {db.name}.{CUSTOMERS}")
    check(checks, "customers.attributes_folded.distinct_eav_ids", expected_eav,
          folded["distinct"],
          f"distinct attributes_entries.eav_id in {db.name}.{CUSTOMERS}")

    lists_as_array = count_typed(db, CUSTOMERS, args.ns, "related_acct_ids", "array")
    lists_present = db[CUSTOMERS].count_documents(
        {"ns": args.ns, "related_acct_ids": {"$exists": True}})
    check(checks, "customers.csv_lists_are_arrays", lists_present, lists_as_array,
          f"$type:array vs $exists on {db.name}.{CUSTOMERS}.related_acct_ids")
    check(checks, "customers.csv_lists_never_scalar", 0,
          db[CUSTOMERS].count_documents({"ns": args.ns, "$expr": {"$and": [
              {"$ne": [{"$type": "$related_acct_ids"}, "missing"]},
              {"$ne": [{"$type": "$related_acct_ids"}, "array"]}]}}),
          f"$expr $type of {db.name}.{CUSTOMERS}.related_acct_ids that is "
          f"present but not an array")
    check(checks, "customers.csv_list_items_are_account_ids", 0,
          db[CUSTOMERS].count_documents({"ns": args.ns, "related_acct_ids": {
              "$elemMatch": {"$not": {"$regex": "^[0-9]+$"}}}}),
          f"array elements of {db.name}.{CUSTOMERS}.related_acct_ids that are "
          f"not bare digit strings")
    check(checks, "customers.malformed_lists_absent_not_truncated", 0,
          db[CUSTOMERS].count_documents({
              "ns": args.ns,
              "quarantine_anomaly_ids": ANOMALY_MALFORMED_RELATED_ACCT_IDS,
              "related_acct_ids": {"$exists": True}}),
          f"{db.name}.{CUSTOMERS} docs quarantined for a malformed list that "
          f"still carry the field")

    dates_present = db[CUSTOMERS].count_documents(
        {"ns": args.ns, "signup_date": {"$exists": True}})
    check(checks, "customers.dates_are_bson", dates_present,
          count_typed(db, CUSTOMERS, args.ns, "signup_date", "date"),
          f"$type:date vs $exists on {db.name}.{CUSTOMERS}.signup_date")
    check(checks, "customers.dates_not_strings", 0,
          count_typed(db, CUSTOMERS, args.ns, "signup_date", "string"),
          f"$type:string on {db.name}.{CUSTOMERS}.signup_date")
    check(checks, "customers.dirty_dates_never_fail_open", 0,
          db[CUSTOMERS].count_documents({
              "ns": args.ns,
              "quarantine_anomaly_ids": ANOMALY_DIRTY_SIGNUP_DT,
              "signup_date": {"$exists": True}}),
          f"{db.name}.{CUSTOMERS} docs quarantined for a dirty date that still "
          f"carry signup_date")

    quarantined = db[QUARANTINE].count_documents({"ns": args.ns})
    named = db[QUARANTINE].count_documents({
        "ns": args.ns,
        "source.primary_key.column": {"$exists": True},
        "source.primary_key.value": {"$type": "string"},
        "anomalies.0.anomaly_id": {"$exists": True},
    })
    check(checks, "customers.quarantine_enumerated", quarantined, named,
          f"{db.name}.{QUARANTINE} docs naming source PK + anomaly id")
    resolvable = sum(
        1 for doc in db[QUARANTINE].find({"ns": args.ns},
                                         {"source.primary_key.value": 1})
        if db[CUSTOMERS].count_documents(
            {"_id": doc["source"]["primary_key"]["value"], "ns": args.ns}) == 1)
    check(checks, "customers.quarantine_pk_resolves", quarantined, resolvable,
          f"each {QUARANTINE}.source.primary_key.value resolves to one "
          f"{CUSTOMERS}._id")

    null_filled = {
        path: count_typed(db, CUSTOMERS, args.ns, path, "null")
        for path in SPARSE_PATHS
    }
    check(checks, "customers.sparse_fields_absent_not_null", 0,
          sum(null_filled.values()),
          f"$type:null across {len(SPARSE_PATHS)} sparse paths in "
          f"{db.name}.{CUSTOMERS}")

    check(checks, "customers.namespace_isolated", 0,
          db[CUSTOMERS].count_documents({"ns": {"$ne": args.ns}})
          + db[QUARANTINE].count_documents({"ns": {"$ne": args.ns}}),
          f"documents of another namespace in {db.name}")
    check(checks, "customers.validator_rejects_bad_document", True,
          validator_rejects(db, args.ns),
          f"insert of a dirty-date/string-list document into {db.name}."
          f"{CUSTOMERS} is refused by $jsonSchema")

    before = fingerprint(db, args.ns)
    rerun: dict[str, Any] = {"performed": True}
    if args.rerun_migration:
        summary = run_migration(args.ns)
        after = fingerprint(db, args.ns)
        rerun["result"] = "pass" if after == before else "fail"
        rerun["evidence"] = (
            f"migration rerun for ns={args.ns}; canonical-JSON md5 per collection "
            f"before={json.dumps(before, sort_keys=True)} "
            f"after={json.dumps(after, sort_keys=True)}; rerun summary "
            f"{' '.join(summary.split())}")
        check(checks, "customers.idempotent", before, after,
              "canonical extended-JSON md5 of both collections around a "
              "second migration run")
    else:
        rerun["result"] = "fail"
        rerun["evidence"] = ("no rerun performed in this invocation: pass "
                             "--rerun-migration to execute one")
        check(checks, "customers.idempotent", "rerun", "not performed",
              "recon invoked without --rerun-migration")

    actual = anomaly_set(db, args.ns)
    expected = [f"dirty_dates:{_manifest_anomaly(args, 'dirty_dates')}",
                f"malformed_csv_lists:{_manifest_anomaly(args, 'malformed_csv_lists')}"]
    detections = {
        "expected_set": sorted(expected),
        "actual_set": sorted(actual),
        "missing": sorted(set(expected) - set(actual)),
        "unexpected": sorted(set(actual) - set(expected)),
    }
    check(checks, "customers.planted_anomalies_exact_set", detections["expected_set"],
          detections["actual_set"],
          f"{db.name}.{QUARANTINE} counts per anomaly id vs manifest "
          f"planted_anomalies")

    report = {
        "kind": "recon-report",
        "unit": UNIT,
        "namespace": args.ns,
        "generated_at": _dt.datetime.now(_dt.timezone.utc)
                            .replace(microsecond=0).isoformat()
                            .replace("+00:00", "Z"),
        "run_mode": args.run_mode,
        # Verbatim command that recomputes every number above against any target
        # the URI points at, including Atlas (the parent's live window).
        "recompute_command": (
            "MONGO_URI='<target-uri>' MONGO_DB=ow_tp_demo uv run --no-project "
            "--with pymongo==4.10.1 --with oracledb==2.5.1 "
            "python3 scripts/tp_mongo/recon_customers.py "
            f"--ns {args.ns} --run-mode live --rerun-migration "
            f"--out docs/tech-partnerships/recon/{UNIT}.recon.json"),
        "checks": checks,
        "values_recomputed_from_target": True,
        "idempotency_rerun": rerun,
        "planted_anomaly_detections": detections,
        "unverified_paths": [] if args.run_mode == "live" else [
            f"Atlas write path: every number above was recomputed from the local "
            f"mongo:7 fixture ({db.name}), not from the shared Atlas cluster, "
            f"which this unit is not permitted to touch.",
            "Atlas validator DDL: create_collection/collMod applying the "
            "$jsonSchema validators was executed against the fixture only; the "
            "parent's preflight verified the equivalent DDL on the cluster.",
            "Atlas index builds (ns, cust_no unique, tenant_id, "
            "anomalies.anomaly_id) are unverified on the cluster; a pre-existing "
            "duplicate cust_no there would surface only on the live run.",
            "Atlas-side performance/timing of the 25k-document load is unmeasured.",
        ],
    }
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
        failed = [c["id"] for c in checks if c["result"] != "pass"]
        print(f"wrote {args.out}: {len(checks) - len(failed)}/{len(checks)} checks "
              f"passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    else:
        print(text, end="")
    client.close()
    return 0 if all(c["result"] == "pass" for c in checks) else 1


def _manifest_anomaly(args: argparse.Namespace, kind: str) -> int:
    """Expected planted-anomaly count, read from the immutable seed manifest."""
    manifest = json.loads((Path(args.manifest) / f"{args.ns}.json").read_text())
    for entry in manifest["planted_anomalies"]:
        if entry["kind"] == kind:
            return int(entry["count"])
    raise SystemExit(f"manifest has no planted anomaly of kind {kind!r}")


if __name__ == "__main__":
    raise SystemExit(main())

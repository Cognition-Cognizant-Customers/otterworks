#!/usr/bin/env python3
"""Reconcile Atlas `customers` against the legacy manifest.

    make mongo-tp-customers-recon NS=demo
    make mongo-tp-customers-recon NS=demo REPORT=docs/tech-partnerships/recon/mongo-customers.md

Every number here is recomputed FROM ATLAS — the manifest
(`testdata/legacy/manifests/<ns>.json`, runtime state, never committed) is read
only for the expected side of each comparison. The report is written to stdout
and, with `--report`, committed as markdown.

Checks: document count, the ordered md5 source-parity checksum, the EAV fold
accounting, and the anomaly ledger (counts + affected `CUST_ID`s).
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
import transform

MANIFEST_DIR = Path(__file__).resolve().parents[3] / "testdata/legacy/manifests"
CUSTOMER_TARGET = "oracle.OW_BILLING.CUSTOMER_MASTER"
EAV_TARGET = "oracle.OW_BILLING.ENTITY_ATTR_VALUE"

# Attribute accounting the contract fixes for NS=demo: the seeded EAV rows are
# not unique per (ENTITY_ID, ATTR_NAME), so name-keyed `attributes` cannot hold
# all 8,333 rows — folded keys + preserved conflicts must account for them.
# Keyed by (namespace, seeded scale): the fold totals follow the seeded row
# count (`n_eav = n_cust // 3`), so they are only valid for the scale they were
# captured at. The scale-free invariant (folded keys + conflicts == manifest EAV
# rows) is always checked.
EXPECTED_EAV = {
    ("demo", "demo"): {"folded_keys": 8141, "conflicts": 192,
                       "customers_with_attributes": 7075},
}

ID_SAMPLE = 10


def load_manifest(ns: str) -> dict:
    path = MANIFEST_DIR / f"{ns}.json"
    if not path.exists():
        raise SystemExit(f"manifest not found: {path} "
                         f"(run `make oracle-billing-seed NS={ns}` first)")
    manifest = json.loads(path.read_text())
    targets = manifest.get("targets", {})
    if CUSTOMER_TARGET not in targets or EAV_TARGET not in targets:
        raise SystemExit(f"{path} has no oracle.* targets "
                         f"(run `make oracle-billing-seed NS={ns}` first)")
    return manifest


def atlas_checksum(collection, ns: str):
    """Ordered md5 over `_id` + `balances.current`, streamed in `_id` order."""
    digest = hashlib.md5()
    count = 0
    cursor = collection.find({"_migration.ns": ns},
                             {"balances.current": 1}).sort("_id", 1)
    for doc in cursor:
        current = doc.get("balances", {}).get("current", 0.0)
        digest.update(f"{doc['_id']}:{float(current):.2f}\n".encode())
        count += 1
    return digest.hexdigest(), count


def attribute_accounting(collection, ns: str) -> dict:
    """Fold accounting recomputed from the stored documents."""
    pipeline = [
        {"$match": {"_migration.ns": ns}},
        {"$project": {
            "keys": {"$size": {"$objectToArray": {"$ifNull": ["$attributes", {}]}}},
            "conflicts": {"$size": {"$ifNull": ["$legacy.attributeConflicts", []]}},
        }},
        {"$group": {
            "_id": None,
            "folded_keys": {"$sum": "$keys"},
            "conflicts": {"$sum": "$conflicts"},
            "customers_with_attributes": {
                "$sum": {"$cond": [{"$gt": ["$keys", 0]}, 1, 0]}},
        }},
    ]
    result = next(iter(collection.aggregate(pipeline)), None) or {}
    folded = result.get("folded_keys", 0)
    conflicts = result.get("conflicts", 0)
    return {"folded_keys": folded, "conflicts": conflicts,
            "source_rows": folded + conflicts,
            "customers_with_attributes":
                result.get("customers_with_attributes", 0)}


def anomaly_ledger(db, ns: str) -> dict:
    """Quarantined fields grouped by kind, with the affected `CUST_ID`s."""
    ledger = {}
    for kind in (transform.KIND_DIRTY_DATE, transform.KIND_MALFORMED_CSV):
        docs = list(db[config.QUARANTINE]
                    .find({"kind": kind, "_migration.ns": ns})
                    .sort("_id", 1))
        ids = sorted({d["custId"] for d in docs})
        fields = sorted({d["field"] for d in docs})
        # every ledger entry's own customer must still exist and keep that
        # exact field raw, so the check stays exact when one customer has
        # several quarantined fields of the same kind
        quarantined = {
            doc["_id"]: set(doc.get("_quarantine", {}))
            for doc in db[config.CUSTOMERS].find({"_id": {"$in": ids}},
                                                 {"_quarantine": 1})
        }
        preserved = sum(1 for d in docs
                        if d["field"] in quarantined.get(d["custId"], ()))
        ledger[kind] = {"count": len(docs), "fields": fields,
                        "cust_ids": ids, "raw_preserved": preserved}
    return ledger


def _check(checks, name, expected, actual, detail=""):
    checks.append({"check": name, "expected": expected, "actual": actual,
                   "ok": expected == actual, "detail": detail})


def reconcile(db, ns: str, manifest: dict) -> dict:
    targets = manifest["targets"]
    expected_customers = targets[CUSTOMER_TARGET]["rows"]
    expected_checksum = targets[CUSTOMER_TARGET]["checksum"]
    expected_eav_rows = targets[EAV_TARGET]["rows"]
    scale = (manifest.get("seed_legacy_params", {})
             .get(EAV_TARGET, {}).get("scale"))
    expected_eav = dict(EXPECTED_EAV.get((ns, scale), {}))
    expected_eav["source_rows"] = expected_eav_rows
    planted = {a["kind"]: a for a in manifest["planted_anomalies"]
               if a["target"].startswith(CUSTOMER_TARGET)}

    customers = db[config.CUSTOMERS]
    doc_count = customers.count_documents({"_migration.ns": ns})
    checksum, checksummed = atlas_checksum(customers, ns)
    attributes = attribute_accounting(customers, ns)
    ledger = anomaly_ledger(db, ns)

    checks = []
    _check(checks, "customers documents", expected_customers, doc_count,
           "manifest rows vs. count_documents in Atlas")
    _check(checks, "source-parity checksum", expected_checksum, checksum,
           f"ordered md5 over {checksummed} documents sorted by _id")
    for key in ("source_rows", "folded_keys", "conflicts",
                "customers_with_attributes"):
        if key in expected_eav:
            _check(checks, f"EAV {key.replace('_', ' ')}",
                   expected_eav[key], attributes[key],
                   "recomputed from attributes + legacy.attributeConflicts")
    if len(expected_eav) == 1:
        print(f"[recon] no pinned fold totals for ns={ns} scale={scale}: "
              f"checking the source-row invariant only", file=sys.stderr)
    _check(checks, "quarantine ledger documents",
           sum(a["count"] for a in planted.values()),
           db[config.QUARANTINE].count_documents({"_migration.ns": ns}),
           "no stale or duplicated ledger entries after reruns")
    for kind, anomaly in sorted(planted.items()):
        entry = ledger[kind]
        column = anomaly["target"].rsplit(".", 1)[-1]
        _check(checks, f"anomaly {kind} ({column})", anomaly["count"],
               entry["count"], f"quarantine ledger, fields={entry['fields']}")
        _check(checks, f"anomaly {kind}: raw value preserved on the customer",
               entry["count"], entry["raw_preserved"],
               "customer document still present with _quarantine.<field>")

    return {
        "ns": ns,
        "database": db.name,
        "generated_at": datetime.now(timezone.utc)
                        .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "manifest_generated_at": manifest["generated_at"],
        "checks": checks,
        "atlas": {"documents": doc_count, "checksum": checksum,
                  "attributes": attributes},
        "anomalies": ledger,
        "passed": all(c["ok"] for c in checks),
    }


def render(result: dict) -> str:
    mark = {True: "PASS", False: "FAIL"}
    lines = [
        f"# Recon — `mongo-customers` (NS={result['ns']})",
        "",
        f"- Target: `{result['database']}.{config.CUSTOMERS}` / "
        f"`{result['database']}.{config.QUARANTINE}`",
        f"- Source of truth: `testdata/legacy/manifests/{result['ns']}.json` "
        f"(generated {result['manifest_generated_at']})",
        f"- Recomputed from Atlas at {result['generated_at']}",
        f"- Verdict: **{mark[result['passed']]}** "
        f"({sum(c['ok'] for c in result['checks'])}/"
        f"{len(result['checks'])} checks)",
        "",
        "## Counts and checksum",
        "",
        "| Check | Expected | From Atlas | Result | How |",
        "|---|---|---|---|---|",
    ]
    for check in result["checks"]:
        lines.append(f"| {check['check']} | `{check['expected']}` | "
                     f"`{check['actual']}` | {mark[check['ok']]} | "
                     f"{check['detail']} |")

    lines += ["", "## Anomaly ledger", "",
              "Quarantined *fields*: the customer document is still migrated "
              "and counted, with the offending value preserved raw under "
              "`_quarantine.<COLUMN>` and the parsed field omitted.", ""]
    for kind, entry in sorted(result["anomalies"].items()):
        ids = entry["cust_ids"]
        lines += [f"### `{kind}` — {entry['count']} "
                  f"(fields: {', '.join(entry['fields']) or 'none'})", ""]
        if ids:
            lines.append(f"First {min(ID_SAMPLE, len(ids))} affected "
                         f"`CUST_ID`s:")
            lines += [f"- `{cust_id}`" for cust_id in ids[:ID_SAMPLE]]
            lines += ["", "<details><summary>all "
                      f"{len(ids)} affected `CUST_ID`s</summary>", "",
                      "```", *ids, "```", "", "</details>", ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default=config.namespace())
    ap.add_argument("--report", help="also write the markdown report here")
    args = ap.parse_args()

    manifest = load_manifest(args.ns)
    client = config.mongo_client()
    try:
        result = reconcile(client[config.database_name()], args.ns, manifest)
    finally:
        client.close()

    report = render(result)
    print(report)
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report)
        print(f"[recon] wrote {path}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

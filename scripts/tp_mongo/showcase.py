#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pymongo==4.10.1"]
# ///
"""Showcase the migrated OtterWorks MongoDB estate: enforced document contracts,
one-pipeline reporting, and the recon/failure loop.

Everything is namespace-scoped (`--ns`): databases are `ow_tp_mongodb_<ns>` and
`ow_tp_mongodb_<ns>_quarantine`. DDL (collMod) runs only on the named
namespace's own collections.

  validators     apply the $jsonSchema validators (collMod) to customers+invoices
  validate-demo  the stage moment: conforming writes land, contract violations
                 (a DD-MON-YY string date, a 156th ad-hoc field, a CSV blob)
                 are rejected by the database itself

Credentials come from MONGODB_ATLAS_URI (or MONGODB_URI / --mongodb-uri); the
tool never prints the URI.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from bson.decimal128 import Decimal128
from pymongo import MongoClient
from pymongo.errors import WriteError

sys.path.insert(0, str(Path(__file__).resolve().parent))

from validators import VALIDATORS

REPO = Path(__file__).resolve().parents[2]
DB_PREFIX = "ow_tp_mongodb_"
PROBE_PREFIX = "showcase-probe"


def require_ns(ns: str) -> str:
    if not ns or not ns.replace("_", "").isalnum() or not ns.islower():
        raise SystemExit(f"--ns must be a lowercase identifier, got {ns!r}")
    return ns


def connect(args) -> MongoClient:
    uri = args.mongodb_uri or os.getenv("MONGODB_ATLAS_URI") or os.getenv("MONGODB_URI")
    if not uri:
        raise SystemExit("set MONGODB_ATLAS_URI (or MONGODB_URI, or pass --mongodb-uri)")
    return MongoClient(uri)


def database(client: MongoClient, ns: str):
    return client[f"{DB_PREFIX}{ns}"]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- validators --------------------------------------------------------------
def apply_validators(client: MongoClient, ns: str) -> None:
    """collMod the namespace's own collections only, after proving the entire
    existing population already satisfies the schema (a validator that the
    migrated data cannot pass would be a lie, not a contract)."""
    db = database(client, ns)
    for name, schema in VALIDATORS.items():
        nonconforming = db[name].count_documents(
            {"$nor": [{"$jsonSchema": schema}]}, limit=1
        )
        if nonconforming:
            sample = db[name].find_one({"$nor": [{"$jsonSchema": schema}]}, {"_id": 1})
            raise SystemExit(
                f"{name}: existing documents do not satisfy the proposed validator "
                f"(e.g. _id={sample['_id']!r}); refusing to collMod"
            )
        db.command(
            "collMod",
            name,
            validator={"$jsonSchema": schema},
            validationLevel="strict",
            validationAction="error",
        )
        print(f"validator applied: {db.name}.{name} (strict/error)")


def cmd_validators(client: MongoClient, args) -> int:
    apply_validators(client, args.ns)
    return 0


# --- validate-demo -----------------------------------------------------------
def conforming_customer(ns: str) -> dict:
    return {
        "_id": f"{ns}:{PROBE_PREFIX}:customer",
        "ns": ns,
        "tenant_id": "00000000-0000-0000-0000-00000000f00d",
        "cust_no": f"{ns.upper()}-99999901",
        "name": f"{ns}::showcase-probe",
        "legal_name": f"{ns}::showcase-probe LLC",
        "email": "billing@showcase-probe.example",
        "address": {"line1": "1 Contract Way", "city": "Fairview", "state": "TN", "zip": "37062"},
        "phones": [{"number": "615-555-0100", "type_cd": 1}],
        "status": {"status_cd": 1, "sub_status_cd": 1, "cust_type_cd": 1, "segment_cd": 1, "region_cd": 1},
        "flags": {"tax_exempt": False, "credit_hold": False, "vip": False},
        "balances": {"current": 0.0, "past_due": 0.0, "ytd_billed": 0.0, "credit_limit": 1000.0},
        "lineage": {"legacy_sys_key": "SHOWCASE", "created_by": "showcase", "row_version_no": 1},
        "signup_dt": datetime(2026, 2, 1, tzinfo=timezone.utc),
        "last_activity_dt": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "related_acct_ids": ["ACCT-0001", "ACCT-0002"],
        "promo_codes": ["WELCOME10"],
    }


def conforming_invoice(ns: str) -> dict:
    def line(no: int, amount: str) -> dict:
        return {
            "line_id": f"{ns}:{PROBE_PREFIX}:line:{no}",
            "line_no": Decimal128(str(no)),
            "line_type_cd": Decimal128("1"),
            "item_desc": "Showcase subscription",
            "qty": Decimal128("1"),
            "unit_price": Decimal128(amount),
            "amount": Decimal128(amount),
            "tax_amt": Decimal128("0.00"),
            "invoice_dt": "01-FEB-26",
            "service_period": "022026-022026",
            "src_system": "MAINFRAME",
            "posted_yn": "Y",
            "gl_accts": ["40001"],
            "cust_no": f"{ns.upper()}-99999901",
            "cust_name": f"{ns}::showcase-probe",
        }

    return {
        "_id": f"{ns}:{PROBE_PREFIX}:invoice",
        "ns": ns,
        "tenant_id": "00000000-0000-0000-0000-00000000f00d",
        "cust_id": f"{ns}:{PROBE_PREFIX}:customer",
        "invoice_no": f"{ns.upper()}-SHOWCASE-01",
        "invoice_dt": "01-FEB-26",
        "due_dt": "03-MAR-26",
        "status_cd": Decimal128("20"),
        "total_amt": Decimal128("49.00"),
        "batch_no": Decimal128("1"),
        "lines": [line(1, "49.00")],
    }


def probe_insert(collection, doc: dict, expect: str) -> dict:
    """Insert (and always clean up) one probe document; report what the
    database decided. expect is 'accept' or 'reject'."""
    outcome: dict = {"collection": collection.name, "_id": doc["_id"], "expected": expect}
    try:
        collection.insert_one(doc)
        outcome["result"] = "accepted"
    except WriteError as exc:
        outcome["result"] = "rejected"
        outcome["code"] = exc.code
        details = (exc.details or {}).get("errInfo", {}).get("details", {})
        outcome["rule_violations"] = [
            rule.get("operatorName", "?")
            for rule in details.get("schemaRulesNotSatisfied", [])
        ]
        outcome["error"] = str((exc.details or {}).get("errmsg", exc))
    finally:
        collection.delete_many({"_id": doc["_id"]})
    outcome["ok"] = (
        outcome["result"] == "accepted" if expect == "accept" else outcome["result"] == "rejected"
    )
    return outcome


def cmd_validate_demo(client: MongoClient, args) -> int:
    ns = args.ns
    db = database(client, ns)
    apply_validators(client, ns)

    probes: list[tuple[str, str, dict, str]] = []

    good_customer = conforming_customer(ns)
    probes.append(("conforming customer document", "customers", good_customer, "accept"))

    legacy_date = copy.deepcopy(good_customer)
    legacy_date["signup_dt"] = "03-FEB-09"
    probes.append((
        "legacy DD-MON-YY string where a BSON date is required",
        "customers", legacy_date, "reject",
    ))

    adhoc_field = copy.deepcopy(good_customer)
    adhoc_field["y2k_verified_flag_2"] = "Y"
    probes.append((
        "a 156th ad-hoc field (the legacy table would have taken it)",
        "customers", adhoc_field, "reject",
    ))

    csv_blob = copy.deepcopy(good_customer)
    csv_blob["related_acct_ids"] = "A1001,A1002,A1003"
    probes.append((
        "CSV blob where the contract demands an array",
        "customers", csv_blob, "reject",
    ))

    good_invoice = conforming_invoice(ns)
    probes.append(("conforming invoice with embedded lines", "invoices", good_invoice, "accept"))

    iso_date = copy.deepcopy(good_invoice)
    iso_date["invoice_dt"] = "2026-02-01"
    probes.append((
        "invoice date breaking the pinned legacy format",
        "invoices", iso_date, "reject",
    ))

    csv_line = copy.deepcopy(good_invoice)
    csv_line["lines"][0]["gl_acct_csv"] = "40001,40002"
    probes.append((
        "embedded line resurrecting the GL_ACCT_CSV column",
        "invoices", csv_line, "reject",
    ))

    results = []
    for title, coll, doc, expect in probes:
        outcome = probe_insert(db[coll], doc, expect)
        outcome["probe"] = title
        results.append(outcome)
        verdict = "OK " if outcome["ok"] else "FAIL"
        print(f"[{verdict}] {coll}: {title} -> {outcome['result']}"
              + (f" ({', '.join(outcome.get('rule_violations', []))})"
                 if outcome["result"] == "rejected" else ""))

    residue = sum(
        db[coll].count_documents({"_id": {"$regex": f":{PROBE_PREFIX}:"}})
        for coll in VALIDATORS
    )
    report = {
        "kind": "validator-demo",
        "namespace": ns,
        "generated_at": now(),
        "collections": {
            name: {
                "validation_level": "strict",
                "validation_action": "error",
                "required": VALIDATORS[name]["required"],
                "additional_properties": VALIDATORS[name]["additionalProperties"],
            }
            for name in VALIDATORS
        },
        "probes": results,
        "probe_residue_documents": residue,
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(f"report: {out}")
    failed = [r["probe"] for r in results if not r["ok"]]
    if failed:
        print(f"validate-demo FAILED probes: {failed}")
    if residue:
        print(f"validate-demo left {residue} probe documents behind (unexpected)")
    return 0 if not failed and not residue else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--mongodb-uri", default="")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validators")
    demo = sub.add_parser("validate-demo")
    demo.add_argument("--out", default="")

    args = parser.parse_args()
    args.ns = require_ns(args.ns)
    commands = {
        "validators": cmd_validators,
        "validate-demo": cmd_validate_demo,
    }
    return commands[args.command](connect(args), args)


if __name__ == "__main__":
    raise SystemExit(main())

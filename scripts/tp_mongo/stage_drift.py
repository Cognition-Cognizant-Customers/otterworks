"""Stage REAL drift in a migrated namespace, the way a bad migration would.

This mutates target data in MongoDB: it drops a deterministic slice of documents,
rounds money off its Decimal128 precision, truncates embedded invoice lines, or
empties a quarantine collection. Nothing here touches the immutable legacy
baseline, no recon report is edited, and the recon job has no "fail on purpose"
switch: the drift is a genuine discrepancy that reconciliation has to find on its
own.

The only way back is re-running the migration for the namespace, which is exactly
the repair story the demo tells.

Refuses to run against the persistent showcase namespace without --force.

Usage:
    MONGO_URI=... python3 scripts/tp_mongo/stage_drift.py --ns drift01 \
        --mutation drop_documents --count 25
"""

from __future__ import annotations

import argparse
import decimal
import sys
from pathlib import Path
from typing import Any

from bson import Decimal128

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mongo_common import (  # noqa: E402
    database_name,
    mongo_client,
    mongo_uri,
    validate_ns,
)
from platform_common import namespace_filter  # noqa: E402

# The persistent showcase namespace: mutating it silently would poison the demo.
PROTECTED_NAMESPACES = frozenset({"demo"})

MUTATIONS = (
    "drop_documents",
    "drop_invoices",
    "round_invoice_money",
    "truncate_invoice_lines",
    "purge_customer_quarantine",
)


def selected_ids(collection: Any, ns_filter: dict[str, str], count: int) -> list[str]:
    """Pick a deterministic slice: the lowest `_id` values in the namespace."""
    return [
        document["_id"]
        for document in collection.find(ns_filter, {"_id": 1}).sort("_id", 1).limit(count)
    ]


def drop_documents(database: Any, ns: str, count: int) -> dict[str, Any]:
    """A lost batch: documents that were migrated are simply gone."""
    ns_filter = namespace_filter("documents", ns)
    ids = selected_ids(database["documents"], ns_filter, count)
    result = database["documents"].delete_many({"_id": {"$in": ids}})
    return {
        "collection": "documents",
        "documents_deleted": result.deleted_count,
        "sample_ids": ids[:5],
    }


def drop_invoices(database: Any, ns: str, count: int) -> dict[str, Any]:
    """A lost batch on the invoice side, taking its embedded lines with it."""
    ns_filter = namespace_filter("invoices", ns)
    ids = selected_ids(database["invoices"], ns_filter, count)
    lines = 0
    for document in database["invoices"].find({"_id": {"$in": ids}}, {"lines": 1}):
        lines += len(document.get("lines", []))
    result = database["invoices"].delete_many({"_id": {"$in": ids}})
    return {
        "collection": "invoices",
        "invoices_deleted": result.deleted_count,
        "embedded_lines_lost": lines,
        "sample_ids": ids[:5],
    }


def round_invoice_money(database: Any, ns: str, count: int) -> dict[str, Any]:
    """Money precision loss: totals rounded to whole currency units.

    Still Decimal128, so the validator has nothing to object to - the values are
    simply wrong, which is precisely the class of drift a schema cannot catch and
    reconciliation must.
    """
    ns_filter = namespace_filter("invoices", ns)
    ids = selected_ids(database["invoices"], ns_filter, count)
    changed = 0
    for document in database["invoices"].find(
        {"_id": {"$in": ids}}, {"total_amt": 1, "tax_amt": 1, "lines.line_id": 1,
                                "lines.amount": 1}
    ):
        total = document["total_amt"].to_decimal().quantize(decimal.Decimal("1"))
        tax = document["tax_amt"].to_decimal().quantize(decimal.Decimal("1"))
        updates: dict[str, Any] = {
            "total_amt": Decimal128(total),
            "tax_amt": Decimal128(tax),
        }
        for index, line in enumerate(document.get("lines", [])):
            amount = line.get("amount")
            if amount is None:
                continue
            updates[f"lines.{index}.amount"] = Decimal128(
                amount.to_decimal().quantize(decimal.Decimal("1"))
            )
        database["invoices"].update_one({"_id": document["_id"]}, {"$set": updates})
        changed += 1
    return {
        "collection": "invoices",
        "invoices_rounded_to_whole_units": changed,
        "sample_ids": ids[:5],
    }


def truncate_invoice_lines(database: Any, ns: str, count: int) -> dict[str, Any]:
    """Embedded lines silently truncated: header survives, detail does not."""
    ns_filter = namespace_filter("invoices", ns)
    ids = selected_ids(database["invoices"], ns_filter, count)
    removed = 0
    changed = 0
    for document in database["invoices"].find({"_id": {"$in": ids}}, {"lines": 1}):
        lines = document.get("lines", [])
        if not lines:
            continue
        kept = lines[:-1]
        database["invoices"].update_one(
            {"_id": document["_id"]},
            {"$set": {"lines": kept}},
        )
        removed += len(lines) - len(kept)
        changed += 1
    return {
        "collection": "invoices",
        "invoices_touched": changed,
        "embedded_lines_removed": removed,
        "line_count_field_left_stale": True,
        "sample_ids": ids[:5],
    }


def purge_customer_quarantine(database: Any, ns: str, count: int) -> dict[str, Any]:
    """Quarantine emptied: the planted anomalies stop being reported at all."""
    ns_filter = namespace_filter("customers_quarantine", ns)
    ids = selected_ids(database["customers_quarantine"], ns_filter, count)
    result = database["customers_quarantine"].delete_many({"_id": {"$in": ids}})
    return {
        "collection": "customers_quarantine",
        "quarantine_records_deleted": result.deleted_count,
        "sample_ids": ids[:5],
    }


MUTATORS = {
    "drop_documents": drop_documents,
    "drop_invoices": drop_invoices,
    "round_invoice_money": round_invoice_money,
    "truncate_invoice_lines": truncate_invoice_lines,
    "purge_customer_quarantine": purge_customer_quarantine,
}

REPAIR = {
    "drop_documents": "make tp-mongo-documents NS={ns}",
    "drop_invoices": "make tp-mongo-invoices NS={ns}",
    "round_invoice_money": "make tp-mongo-invoices NS={ns}",
    "truncate_invoice_lines": "make tp-mongo-invoices NS={ns}",
    "purge_customer_quarantine": "make tp-mongo-customers NS={ns}",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", required=True)
    parser.add_argument("--mutation", choices=MUTATIONS, default="drop_documents")
    parser.add_argument("--count", type=int, default=25,
                        help="how many documents the mutation affects")
    parser.add_argument("--force", action="store_true",
                        help=f"required to mutate {sorted(PROTECTED_NAMESPACES)}")
    args = parser.parse_args()
    ns = validate_ns(args.ns)
    if args.count < 1:
        raise SystemExit("--count must be at least 1")
    if ns in PROTECTED_NAMESPACES and not args.force:
        raise SystemExit(
            f"refusing to stage drift in the protected namespace '{ns}': it is the "
            "persistent showcase namespace. Rehearse in a throwaway namespace "
            "(e.g. --ns drift01), or pass --force if you really mean to break "
            f"'{ns}' and are ready to repair it with `{REPAIR[args.mutation].format(ns=ns)}`."
        )

    client = mongo_client()
    try:
        database = client[database_name(ns)]
        print(f"staging drift: mutation={args.mutation} ns={ns} "
              f"db={database_name(ns)} uri={mongo_uri()} count={args.count}")
        effect = MUTATORS[args.mutation](database, ns, args.count)
    finally:
        client.close()

    print(f"drift staged in target data: {effect}")
    print("this is a real discrepancy in the migrated data - no report was edited "
          "and reconciliation has no knowledge of it")
    print(f"repair (the only way back): {REPAIR[args.mutation].format(ns=ns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

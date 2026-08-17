#!/usr/bin/env python3
"""Migrate the legacy Oracle customer estate into MongoDB (``mongo_customers``).

Source: ``OW_BILLING.CUSTOMER_MASTER`` (155 sparse columns) plus
``OW_BILLING.ENTITY_ATTR_VALUE`` (EAV sprawl).
Target: ``<db>.customers`` and ``<db>.customers_quarantine``, where ``<db>``
defaults to ``ow_tp_<ns>``.

The target is addressed only through ``MONGO_URI`` (default: the local
``mongo:7`` fixture), so the same code runs against the shared Atlas cluster
with no change. Reruns are idempotent: documents are replaced by their source
primary key and documents of this namespace that no longer exist in the source
are removed, while other namespaces are never touched.

Usage:
    MONGO_URI=mongodb://localhost:27017 \\
      uv run --with pymongo==4.10.1 --with oracledb==2.5.1 \\
      scripts/tp_mongo/migrate_customers.py --ns demo
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import oracledb
from pymongo import MongoClient, ReplaceOne

sys.path.insert(0, str(Path(__file__).resolve().parent))
from customers_model import (  # noqa: E402
    CUSTOMERS_VALIDATOR,
    QUARANTINE_VALIDATOR,
    build_customer,
    build_quarantine,
)

CUSTOMERS = "customers"
QUARANTINE = "customers_quarantine"
DEFAULT_MONGO_URI = "mongodb://localhost:27017"


class EmptySourceRead(RuntimeError):
    """Raised when the source read is empty, which is never a legitimate state."""


def mongo_uri() -> str:
    return os.environ.get("MONGO_URI", DEFAULT_MONGO_URI)


def target_db_name(ns: str) -> str:
    return os.environ.get("MONGO_DB", f"ow_tp_{ns}")


def oracle_connect(args: argparse.Namespace) -> oracledb.Connection:
    oracledb.defaults.fetch_decimals = True
    return oracledb.connect(user=args.oracle_user, password=args.oracle_password,
                            host=args.oracle_host, port=args.oracle_port,
                            service_name=args.oracle_service)


def fetch_attributes(cur: oracledb.Cursor, ns: str) -> dict[str, list[dict[str, Any]]]:
    """Load the EAV rows for this namespace's customers, keyed by customer id."""
    prefix = f"{ns.upper()}-"
    cur.execute(
        """SELECT e.eav_id, e.entity_id, e.attr_name, e.attr_value, e.attr_type
             FROM entity_attr_value e, customer_master c
            WHERE e.entity_type = 'CUSTOMER'
              AND e.entity_id = c.cust_id
              AND SUBSTR(c.cust_no, 1, LENGTH(:pfx)) = :pfx
            ORDER BY e.eav_id""",
        pfx=prefix,
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for eav_id, entity_id, attr_name, attr_value, attr_type in cur:
        out.setdefault(entity_id, []).append({
            "EAV_ID": eav_id, "ATTR_NAME": attr_name,
            "ATTR_VALUE": attr_value, "ATTR_TYPE": attr_type,
        })
    return out


def iter_customers(cur: oracledb.Cursor, ns: str) -> Iterator[dict[str, Any]]:
    """Stream every ``CUSTOMER_MASTER`` row of this namespace, all 155 columns."""
    prefix = f"{ns.upper()}-"
    cur.arraysize = 1000
    cur.execute(
        """SELECT * FROM customer_master
            WHERE SUBSTR(cust_no, 1, LENGTH(:pfx)) = :pfx
            ORDER BY cust_id""",
        pfx=prefix,
    )
    columns = [d.name for d in cur.description]
    for row in cur:
        yield dict(zip(columns, row))


def ensure_collection(db, name: str, validator: dict[str, Any]) -> None:
    """Create or update the collection's ``$jsonSchema`` validator in place.

    Never drops or recreates a collection, so a rerun keeps existing evidence.
    """
    options = {"validator": validator, "validationLevel": "strict",
               "validationAction": "error"}
    if name in db.list_collection_names():
        db.command({"collMod": name, **options})
    else:
        db.create_collection(name, **options)


def flush(collection, ops: list[ReplaceOne]) -> None:
    if ops:
        collection.bulk_write(ops, ordered=False)
        ops.clear()


def migrate(args: argparse.Namespace) -> dict[str, Any]:
    db_name = target_db_name(args.ns)
    client: MongoClient = MongoClient(mongo_uri(), tz_aware=False)
    db = client[db_name]
    ensure_collection(db, CUSTOMERS, CUSTOMERS_VALIDATOR)
    ensure_collection(db, QUARANTINE, QUARANTINE_VALIDATOR)
    db[CUSTOMERS].create_index("ns")
    db[CUSTOMERS].create_index("cust_no", unique=True)
    db[CUSTOMERS].create_index("tenant_id")
    db[QUARANTINE].create_index("ns")
    db[QUARANTINE].create_index("anomalies.anomaly_id")

    conn = oracle_connect(args)
    cur = conn.cursor()
    attributes = fetch_attributes(cur, args.ns)

    cust_ops: list[ReplaceOne] = []
    quar_ops: list[ReplaceOne] = []
    seen_customers: set[str] = set()
    seen_quarantine: set[str] = set()
    anomaly_counts: Counter[str] = Counter()
    read_rows = 0

    for row in iter_customers(cur, args.ns):
        read_rows += 1
        attrs = attributes.get(row["CUST_ID"], [])
        doc, anomalies = build_customer(row, attrs, args.ns)
        seen_customers.add(doc["_id"])
        cust_ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        if anomalies:
            # tolerate-and-attribute: the record migrates with the offending
            # field absent and is enumerated here with its source PK + anomaly id.
            qdoc = build_quarantine(row, attrs, args.ns, anomalies)
            for anomaly in anomalies:
                anomaly_counts[anomaly.anomaly_id] += 1
            seen_quarantine.add(qdoc["_id"])
            quar_ops.append(ReplaceOne({"_id": qdoc["_id"]}, qdoc, upsert=True))
        if len(cust_ops) >= args.batch_size:
            flush(db[CUSTOMERS], cust_ops)
        if len(quar_ops) >= args.batch_size:
            flush(db[QUARANTINE], quar_ops)
    flush(db[CUSTOMERS], cust_ops)
    flush(db[QUARANTINE], quar_ops)
    cur.close()
    conn.close()

    if read_rows == 0:
        # Contract empty_input_semantics: no-op. At demo scale an empty read is a
        # source-connectivity failure, so prior output is left untouched.
        client.close()
        raise EmptySourceRead(
            f"read 0 source rows for ns={args.ns}: refusing to touch "
            f"{db_name}.{CUSTOMERS} (treated as a source-connectivity failure)"
        )

    removed_customers = _delete_stale(db[CUSTOMERS], args.ns, seen_customers)
    removed_quarantine = _delete_stale(db[QUARANTINE], args.ns, seen_quarantine)

    summary = {
        "unit": "mongo_customers",
        "namespace": args.ns,
        "target_database": db_name,
        "source_rows_read": read_rows,
        "written_customers": len(seen_customers),
        "written_quarantine": len(seen_quarantine),
        "removed_stale_customers": removed_customers,
        "removed_stale_quarantine": removed_quarantine,
        "anomalies_by_id": dict(sorted(anomaly_counts.items())),
    }
    client.close()
    return summary


def _delete_stale(collection, ns: str, written: set[str]) -> int:
    """Remove this namespace's documents the source no longer produces.

    The stale set is computed client-side and deleted by explicit ``_id``, so
    the delete filter can never widen to another namespace's documents.
    """
    existing = {doc["_id"] for doc in collection.find({"ns": ns}, {"_id": 1})}
    stale = sorted(existing - written)
    if not stale:
        return 0
    return collection.delete_many({"ns": ns, "_id": {"$in": stale}}).deleted_count


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ns", required=True, help="namespace slice to migrate (e.g. demo)")
    p.add_argument("--batch-size", type=int, default=1000)
    p.add_argument("--oracle-host", default=os.environ.get("DB_HOST", "localhost"))
    p.add_argument("--oracle-port", type=int,
                   default=int(os.environ.get("DB_PORT", "52521")))
    p.add_argument("--oracle-user", default=os.environ.get("DB_USER", "ow_billing"))
    p.add_argument("--oracle-password",
                   default=os.environ.get("DB_PASSWORD", "ow_billing"))
    p.add_argument("--oracle-service", default=os.environ.get("DB_SERVICE", "FREEPDB1"))
    args = p.parse_args()
    try:
        summary = migrate(args)
    except EmptySourceRead as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

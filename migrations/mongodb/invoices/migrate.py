#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["oracledb==2.5.1", "pymongo==4.10.1"]
# ///
"""Migrate OW_BILLING.INVOICE_HEADER + INVOICE_LINE to MongoDB invoices.

Unit: mongo_invoices (contract: docs/tech-partnerships/contracts/mongo_invoices.json).

Every non-orphaned INVOICE_LINE row is embedded in its header document in
`ow_tp_mongodb_<ns>.invoices`; lines referencing a ghost invoice_id are
quarantined with attribution in
`ow_tp_mongodb_<ns>_quarantine.invoice_lines_quarantine` — never dropped and
never embedded in a fabricated header.

Policies (per contract):
- amounts are fetched as decimal.Decimal and stored as BSON Decimal128 —
  never floats, no rounding of cents;
- NULL source columns are omitted fields; a NULL amount is quarantined with
  attribution rather than failing open to 0;
- an empty/absent source namespace is a no-op: nothing is written and prior
  target output is left untouched;
- writes are per-batch bulk upserts keyed on deterministic source PKs, so a
  rerun for the same namespace reproduces identical recon numbers. Headers
  are assembled in memory before flushing (bounded at SCALE=demo: 18,750
  headers / 150,000 lines); SCALE=full would want a streaming merge-join.

Run under scripts/tp-run-deterministic.sh conventions (TZ=UTC LC_ALL=C LANG=C):
  uv run migrations/mongodb/invoices/migrate.py --ns demo
"""

import argparse
import hashlib
import os
import re
import sys
from decimal import Decimal

import oracledb
from bson.decimal128 import Decimal128
from pymongo import MongoClient, ReplaceOne

BATCH_SIZE = 5_000

HEADER_COLS = ["invoice_id", "invoice_no", "cust_id", "tenant_id",
               "invoice_dt", "due_dt", "status_cd", "total_amt", "batch_no"]
LINE_COLS = ["line_id", "invoice_no", "invoice_id", "cust_id", "cust_no",
             "cust_name", "tenant_id", "line_no", "line_type_cd", "item_desc",
             "qty", "unit_price", "amount", "tax_amt", "invoice_dt",
             "service_period", "posted_yn", "gl_acct_csv", "batch_no",
             "src_system"]
# Denormalized per-line copies of identifiers that the header document
# already carries are not embedded; cust_no/cust_name are kept because the
# header has no such columns. Orphans keep every source column in
# quarantine.
EMBED_DROP = {"invoice_no", "invoice_id", "cust_id",
              "tenant_id", "batch_no"}


def ns_seed(ns: str) -> int:
    return int(hashlib.sha256(ns.encode()).hexdigest()[:8], 16)


def ns_batch_no(ns: str) -> int:
    return ns_seed(ns) % 90_000_000 + 1_000_000


def bsonify(value):
    if isinstance(value, Decimal):
        return Decimal128(value)
    return value


def row_doc(cols, row, drop=frozenset()):
    """NULL columns are omitted fields (contract null_attribution)."""
    return {c: bsonify(v) for c, v in zip(cols, row)
            if v is not None and c not in drop}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--mongo-uri",
                    default=os.environ.get("MONGODB_URI",
                                           "mongodb://localhost:27017"))
    ap.add_argument("--host", default=os.environ.get("DB_HOST", "localhost"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("DB_PORT", "52521")))
    ap.add_argument("--user", default=os.environ.get("DB_USER", "ow_billing"))
    ap.add_argument("--password",
                    default=os.environ.get("DB_PASSWORD", "ow_billing"))
    ap.add_argument("--service", default=os.environ.get("DB_SERVICE", "FREEPDB1"))
    args = ap.parse_args()
    ns = args.ns
    if not re.fullmatch(r"[A-Za-z0-9_]{1,11}", ns):
        ap.error("--ns must match [A-Za-z0-9_]{1,11} (seeder valid_ns rule)")

    # Exact numerics: fetch every NUMBER as decimal.Decimal, never float.
    oracledb.defaults.fetch_decimals = True

    batch_no = ns_batch_no(ns)
    conn = oracledb.connect(user=args.user, password=args.password,
                            host=args.host, port=args.port,
                            service_name=args.service)
    cur = conn.cursor()

    cur.execute(f"SELECT {', '.join(HEADER_COLS)} FROM invoice_header "
                "WHERE batch_no = :1 ORDER BY invoice_id", [batch_no])
    headers = {}
    for row in cur:
        doc = row_doc(HEADER_COLS, row)
        doc["_id"] = doc.pop("invoice_id")
        doc["ns"] = ns
        doc["lines"] = []
        headers[doc["_id"]] = doc

    cur.execute("SELECT COUNT(*) FROM invoice_line WHERE batch_no = :1",
                [batch_no])
    n_lines = cur.fetchone()[0]

    if not headers and n_lines == 0:
        # Empty-input semantics: no-op, prior target output untouched.
        print(f"[migrate] ns={ns}: empty source namespace, no-op")
        return 0

    client = MongoClient(args.mongo_uri)
    invoices = client[f"ow_tp_mongodb_{ns}"]["invoices"]
    quarantine = client[f"ow_tp_mongodb_{ns}_quarantine"]["invoice_lines_quarantine"]

    n_embedded = n_quarantined = 0
    quarantined_ids = []
    quarantine_ops = []
    cur.execute(f"SELECT {', '.join(LINE_COLS)} FROM invoice_line "
                "WHERE batch_no = :1 ORDER BY line_id", [batch_no])
    for row in cur:
        line = dict(zip(LINE_COLS, row))
        problems = []
        if line["invoice_id"] is None or line["invoice_id"] not in headers:
            problems.append(("orphaned_rows", "invoice_id references no "
                             "INVOICE_HEADER row in this namespace"))
        if line["amount"] is None:
            problems.append(("null_amount",
                             "NULL amount never fails open to 0"))
        if problems:
            qdoc = row_doc(LINE_COLS, row)
            qdoc["_id"] = qdoc.pop("line_id")
            qdoc["ns"] = ns
            qdoc["quarantine"] = {
                "unit": "mongo_invoices",
                "kind": problems[0][0],
                "reasons": [f"{k}: {d}" for k, d in problems],
                "source": "oracle.OW_BILLING.INVOICE_LINE",
            }
            quarantine_ops.append(ReplaceOne({"_id": qdoc["_id"]}, qdoc,
                                             upsert=True))
            quarantined_ids.append(qdoc["_id"])
            n_quarantined += 1
            if len(quarantine_ops) >= BATCH_SIZE:
                quarantine.bulk_write(quarantine_ops, ordered=True)
                quarantine_ops = []
            continue
        edoc = row_doc(LINE_COLS, row, drop=EMBED_DROP)
        if "gl_acct_csv" in edoc:
            edoc["gl_accts"] = edoc.pop("gl_acct_csv").split(",")
        headers[line["invoice_id"]]["lines"].append(edoc)
        n_embedded += 1
    if quarantine_ops:
        quarantine.bulk_write(quarantine_ops, ordered=True)

    ops = []
    for doc in headers.values():
        ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        if len(ops) >= 1_000:
            invoices.bulk_write(ops, ordered=True)
            ops = []
    if ops:
        invoices.bulk_write(ops, ordered=True)

    # Convergent rerun: prune documents from a prior run of this namespace
    # whose source rows no longer exist or changed classification (e.g. a
    # formerly-orphaned line whose header now exists). The empty-input no-op
    # above returns before this point, so prior output stays untouched then.
    invoices.delete_many({"ns": ns, "_id": {"$nin": list(headers)}})
    quarantine.delete_many({"ns": ns, "_id": {"$nin": quarantined_ids}})

    # Non-unique: the legacy source only guarantees uniqueness on invoice_id
    # (invoice_no is nullable and unconstrained in invoice_header).
    invoices.create_index([("ns", 1), ("invoice_no", 1)])
    invoices.create_index([("ns", 1), ("cust_id", 1)])
    quarantine.create_index([("ns", 1), ("quarantine.kind", 1)])

    cur.close()
    conn.close()
    client.close()
    print(f"[migrate] ns={ns} invoices={len(headers)} "
          f"embedded_lines={n_embedded} quarantined={n_quarantined}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

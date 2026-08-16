#!/usr/bin/env python3
"""mongo_invoices unit: Oracle OW_BILLING invoices -> MongoDB document model.

INVOICE_HEADER rows become one document each in ow_tp_mongodb_<ns>.invoices with
their INVOICE_LINE rows embedded (sorted by line_no, line_id). Lines that
reference a missing header are quarantined with attribution in
ow_tp_mongodb_<ns>_quarantine.invoice_lines_quarantine — never dropped and never
embedded under a fabricated header. NULL source columns are omitted fields;
a NULL amount is attributed, never coerced to 0.

Deterministic and idempotent: documents are keyed on the source primary keys
(deterministic md5-uuids), reruns upsert and prune only ids that vanished from
the same namespace's source batch, and no wall-clock values are embedded.
An empty/absent source namespace is a strict no-op.
"""

import argparse
import decimal
import os
import sys

import oracledb
from bson.decimal128 import Decimal128
from pymongo import MongoClient, ReplaceOne

import tp_common

HEADER_COLS = ["invoice_id", "invoice_no", "cust_id", "tenant_id",
               "invoice_dt", "due_dt", "status_cd", "total_amt", "batch_no"]
LINE_COLS = ["line_id", "invoice_no", "invoice_id", "cust_id", "cust_no",
             "cust_name", "tenant_id", "line_no", "line_type_cd", "item_desc",
             "qty", "unit_price", "amount", "tax_amt", "invoice_dt",
             "service_period", "posted_yn", "gl_acct_csv", "batch_no",
             "src_system"]
DECIMAL_COLS = {"total_amt", "qty", "unit_price", "amount", "tax_amt"}


def raw_fetch_handler(cursor, metadata):
    """NUMBERs come back as exact Decimal; character data is decoded with
    surrogateescape so invalid-UTF-8 source bytes survive the fetch and can be
    quarantined per-row instead of aborting the whole migration."""
    if metadata.type_code is oracledb.DB_TYPE_NUMBER:
        return cursor.var(decimal.Decimal, arraysize=cursor.arraysize)
    if metadata.type_code in (oracledb.DB_TYPE_VARCHAR, oracledb.DB_TYPE_CHAR,
                              oracledb.DB_TYPE_NVARCHAR, oracledb.DB_TYPE_NCHAR,
                              oracledb.DB_TYPE_LONG):
        return cursor.var(str, size=metadata.internal_size or 4000,
                          arraysize=cursor.arraysize,
                          encoding_errors="surrogateescape")
    return None


def pk_str(val) -> str:
    """Primary keys must always be representable, even on a quarantined row."""
    if isinstance(val, str):
        return val.encode("utf-8", "backslashreplace").decode("utf-8")
    return str(val)


def to_doc(cols, row) -> dict:
    """NULL columns are omitted; NUMBER amounts become exact Decimal128;
    strings carrying surrogate-escaped invalid source bytes raise UnicodeError."""
    doc = {}
    for col, val in zip(cols, row):
        if val is None:
            continue
        if isinstance(val, str):
            val.encode("utf-8", errors="strict")  # raises on escaped bad bytes
        if col in DECIMAL_COLS:
            val = Decimal128(val)
        elif isinstance(val, decimal.Decimal):
            val = int(val) if val == val.to_integral_value() else Decimal128(val)
        doc[col] = val
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=52521)
    ap.add_argument("--user", default=os.environ.get("DB_USER", "ow_billing"))
    ap.add_argument("--password",
                    default=os.environ.get("DB_PASSWORD", "ow_billing"))
    ap.add_argument("--service", default="FREEPDB1")
    ap.add_argument("--run-mode", choices=["fixture", "live"], default="fixture")
    args = ap.parse_args()
    ns = args.ns
    if not tp_common.valid_ns(ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    batch = tp_common.batch_no(ns)

    conn = oracledb.connect(user=args.user, password=args.password,
                            host=args.host, port=args.port,
                            service_name=args.service)
    conn.outputtypehandler = raw_fetch_handler
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM invoice_header WHERE batch_no = :1", [batch])
    n_headers = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM invoice_line WHERE batch_no = :1", [batch])
    n_lines = cur.fetchone()[0]
    if n_headers == 0 and n_lines == 0:
        print(f"[migrate] ns={ns}: source namespace is empty/absent; "
              "no-op, target left untouched")
        return 0

    # --- headers ---
    headers: dict[str, dict] = {}
    quarantined_headers: list[dict] = []
    cur.execute(f"SELECT {', '.join(HEADER_COLS)} FROM invoice_header "
                "WHERE batch_no = :1", [batch])
    while rows := cur.fetchmany(5000):
        for row in rows:
            try:
                doc = to_doc(HEADER_COLS, row)
            except UnicodeError:
                quarantined_headers.append(
                    {"_id": pk_str(row[0]), "unit": "mongo_invoices", "ns": ns,
                     "reason": "invalid_utf8", "record_type": "invoice_header",
                     "raw_repr": repr(row)})
                continue
            doc["_id"] = doc.pop("invoice_id")
            doc["ns"] = ns
            doc["lines"] = []
            headers[doc["_id"]] = doc

    # --- lines: embed under their header or quarantine orphans ---
    quarantine: list[dict] = list(quarantined_headers)
    cur.execute(f"SELECT {', '.join(LINE_COLS)} FROM invoice_line "
                "WHERE batch_no = :1", [batch])
    n_embedded = 0
    while rows := cur.fetchmany(10000):
        for row in rows:
            line_id, invoice_id = pk_str(row[0]), pk_str(row[2])
            base = {"_id": line_id, "unit": "mongo_invoices", "ns": ns}
            try:
                line = to_doc(LINE_COLS, row)
            except UnicodeError:
                quarantine.append({**base, "reason": "invalid_utf8",
                                   "raw_repr": repr(row),
                                   "attribution": "source bytes do not decode "
                                                  "as UTF-8; quarantined raw"})
                continue
            except Exception:
                quarantine.append({**base, "reason": "invalid_record",
                                   "raw_repr": repr(row)})
                continue
            if invoice_id not in headers:
                quarantine.append({**base, "reason": "orphaned_line",
                                   "missing_invoice_id": invoice_id,
                                   "line": line,
                                   "attribution": "no matching INVOICE_HEADER row; "
                                                  "quarantined, not embedded"})
                continue
            if "amount" not in line:
                quarantine.append({**base, "reason": "null_amount",
                                   "line": line,
                                   "attribution": "NULL amount is never coerced to 0"})
                continue
            line.pop("line_id")
            line["_line_id"] = line_id
            headers[invoice_id]["lines"].append(line)
            n_embedded += 1
    cur.close()
    conn.close()

    for doc in headers.values():
        doc["lines"].sort(key=lambda l: (int(l.get("line_no", 0)), l["_line_id"]))

    # --- write to Mongo: idempotent upserts keyed on the deterministic ids ---
    client = MongoClient(tp_common.mongo_uri(args.run_mode))
    invoices = client[tp_common.target_db_name(ns)]["invoices"]
    qcoll = client[tp_common.quarantine_db_name(ns)]["invoice_lines_quarantine"]

    def sync(coll, docs_by_id):
        ops = [ReplaceOne({"_id": _id}, doc, upsert=True)
               for _id, doc in docs_by_id.items()]
        for i in range(0, len(ops), 1000):
            coll.bulk_write(ops[i:i + 1000], ordered=False)
        existing = {d["_id"] for d in coll.find({"ns": ns}, {"_id": 1})}
        stale = sorted(existing - set(docs_by_id))
        if stale:
            coll.delete_many({"_id": {"$in": stale}, "ns": ns})
        return len(stale)

    stale_inv = sync(invoices, headers)
    stale_q = sync(qcoll, {d["_id"]: d for d in quarantine})
    n_orphans = sum(1 for d in quarantine if d["reason"] == "orphaned_line")
    print(f"[migrate] ns={ns} invoices={len(headers)} embedded_lines={n_embedded} "
          f"quarantined={len(quarantine)} (orphaned_line={n_orphans}) "
          f"pruned_stale={stale_inv + stale_q}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

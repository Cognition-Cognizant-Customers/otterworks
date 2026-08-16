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

    # --- Mongo sinks: buffered idempotent upserts + stale-id prune at the end,
    # so memory stays bounded regardless of namespace scale ---
    client = MongoClient(tp_common.mongo_uri(args.run_mode))
    invoices = client[tp_common.target_db_name(ns)]["invoices"]
    qcoll = client[tp_common.quarantine_db_name(ns)]["invoice_lines_quarantine"]

    class Sink:
        def __init__(self, coll):
            self.coll, self.buf, self.ids, self.count = coll, [], set(), 0

        def add(self, doc):
            self.ids.add(doc["_id"])
            self.count += 1
            self.buf.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
            if len(self.buf) >= 1000:
                self.coll.bulk_write(self.buf, ordered=False)
                self.buf.clear()

        def finish(self) -> int:
            if self.buf:
                self.coll.bulk_write(self.buf, ordered=False)
                self.buf.clear()
            existing = {d["_id"] for d in self.coll.find({"ns": ns}, {"_id": 1})}
            stale = sorted(existing - self.ids)
            if stale:
                self.coll.delete_many({"_id": {"$in": stale}, "ns": ns})
            return len(stale)

    inv_sink, q_sink = Sink(invoices), Sink(qcoll)
    q_reasons: dict[str, int] = {}

    def quarantine(doc):
        q_reasons[doc["reason"]] = q_reasons.get(doc["reason"], 0) + 1
        q_sink.add(doc)

    # --- headers: keep only the (line-less) header docs in memory ---
    headers: dict[str, dict] = {}
    header_ids: set[str] = set()
    bad_header_ids: set[str] = set()
    cur.execute(f"SELECT {', '.join(HEADER_COLS)} FROM invoice_header "
                "WHERE batch_no = :1", [batch])
    while rows := cur.fetchmany(5000):
        for row in rows:
            try:
                doc = to_doc(HEADER_COLS, row)
            except UnicodeError:
                hid = pk_str(row[0])
                bad_header_ids.add(hid)
                quarantine(
                    {"_id": tp_common.det_id(ns, "quarantine", hid),
                     "source_invoice_id": hid,
                     "unit": "mongo_invoices", "ns": ns,
                     "reason": "invalid_utf8", "record_type": "invoice_header",
                     "raw_repr": repr(row)})
                continue
            inv_id = doc["invoice_id"]
            doc["_id"] = tp_common.det_id(ns, "invoice", inv_id)
            doc["ns"] = ns
            doc["lines"] = []
            headers[inv_id] = doc
            header_ids.add(inv_id)

    # --- lines: streamed in invoice_id order so each invoice document is
    # completed and flushed as soon as its last line arrives ---
    cur.execute(f"SELECT {', '.join(LINE_COLS)} FROM invoice_line "
                "WHERE batch_no = :1 ORDER BY invoice_id", [batch])
    n_embedded = 0
    current_inv: str | None = None

    def flush_invoice(inv_id):
        doc = headers.pop(inv_id, None)
        if doc is not None:
            doc["lines"].sort(key=lambda l: (int(l.get("line_no", 0)),
                                             l["_line_id"]))
            inv_sink.add(doc)

    while rows := cur.fetchmany(10000):
        for row in rows:
            line_id, invoice_id = pk_str(row[0]), pk_str(row[2])
            if invoice_id != current_inv:
                if current_inv is not None:
                    flush_invoice(current_inv)
                current_inv = invoice_id
            base = {"_id": tp_common.det_id(ns, "quarantine", line_id),
                    "source_line_id": line_id,
                    "unit": "mongo_invoices", "ns": ns}
            try:
                line = to_doc(LINE_COLS, row)
            except UnicodeError:
                quarantine({**base, "reason": "invalid_utf8",
                            "raw_repr": repr(row),
                            "attribution": "source bytes do not decode "
                                           "as UTF-8; quarantined raw"})
                continue
            except Exception:
                quarantine({**base, "reason": "invalid_record",
                            "raw_repr": repr(row)})
                continue
            if invoice_id in bad_header_ids:
                quarantine({**base, "reason": "header_quarantined",
                            "header_invoice_id": invoice_id,
                            "line": line,
                            "attribution": "header row exists in source but was "
                                           "quarantined (invalid_utf8); line set "
                                           "aside with it, not an orphan"})
                continue
            if invoice_id not in header_ids:
                quarantine({**base, "reason": "orphaned_line",
                            "missing_invoice_id": invoice_id,
                            "line": line,
                            "attribution": "no matching INVOICE_HEADER row; "
                                           "quarantined, not embedded"})
                continue
            if "amount" not in line:
                quarantine({**base, "reason": "null_amount",
                            "line": line,
                            "attribution": "NULL amount is never coerced to 0"})
                continue
            line.pop("line_id")
            line["_line_id"] = line_id
            headers[invoice_id]["lines"].append(line)
            n_embedded += 1
    if current_inv is not None:
        flush_invoice(current_inv)
    cur.close()
    conn.close()

    for doc in headers.values():  # headers with no lines at all
        inv_sink.add(doc)

    stale = inv_sink.finish() + q_sink.finish()
    n_quarantined = sum(q_reasons.values())
    print(f"[migrate] ns={ns} invoices={inv_sink.count} embedded_lines={n_embedded} "
          f"quarantined={n_quarantined} "
          f"(orphaned_line={q_reasons.get('orphaned_line', 0)}) "
          f"pruned_stale={stale}")
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

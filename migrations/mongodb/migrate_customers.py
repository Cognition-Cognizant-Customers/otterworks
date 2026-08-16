#!/usr/bin/env python3
"""mongo_customers unit: OW_BILLING.CUSTOMER_MASTER + ENTITY_ATTR_VALUE -> customers.

Deterministic, namespaced, idempotent:
- one document per CUSTOMER_MASTER row, `_id` = uuid5 of (ns, cust_id);
- NULL source values are omitted fields, never fabricated defaults;
- ENTITY_ATTR_VALUE rows fold into the owning document's `attributes` array
  (one entry per source row, ordered by eav_id);
- RELATED_ACCT_IDS / PROMO_CODES_CSV become real arrays; malformed CSV lists
  are quarantined with attribution and the field is omitted from the document;
- valid DD-MON-YY VARCHAR2 dates become BSON dates; dirty values are
  quarantined with attribution and the field is omitted;
- a value that does not decode as UTF-8 quarantines the whole record;
- an empty/absent source namespace is a no-op: nothing is written and prior
  target output is left untouched;
- writes are per-batch idempotent upserts (ReplaceOne on deterministic ids),
  so a rerun reproduces identical target state.

Usage: python3 migrate_customers.py --ns demo
Target: MONGODB_URI (defaults to the local fixture, mongodb://localhost:27717).
"""

import argparse
import re
import sys
from datetime import datetime, timezone

from pymongo import ReplaceOne

import mongo_common

UNIT = "mongo_customers"
SOURCE_TABLE = "OW_BILLING.CUSTOMER_MASTER"
BATCH_SIZE = 1000

MONS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
DATE_RE = re.compile(r"^(\d{2})-([A-Z]{3})-(\d{2})$")
CSV_ID_TOKEN = re.compile(r"^\d+$")
CSV_CODE_TOKEN = re.compile(r"^[A-Za-z0-9_]+$")

# VARCHAR2 columns holding DD-MON-YY strings that must become BSON dates.
DATE_FIELDS = ("signup_dt", "last_activity_dt")
# CSV list columns that must become real arrays.
CSV_FIELDS = {"related_acct_ids": CSV_ID_TOKEN, "promo_codes_csv": CSV_CODE_TOKEN}
# Field -> planted-anomaly kind used for quarantine attribution.
QUARANTINE_KINDS = {
    "signup_dt": "dirty_dates",
    "last_activity_dt": "dirty_dates",
    "related_acct_ids": "malformed_csv_lists",
    "promo_codes_csv": "malformed_csv_lists",
}


def parse_legacy_date(value: str):
    """Strict DD-MON-YY parser; returns a BSON-compatible datetime or None."""
    m = DATE_RE.match(value)
    if not m:
        return None
    dd, mon, yy = int(m.group(1)), m.group(2), int(m.group(3))
    if mon not in MONS:
        return None
    year = 2000 + yy if yy <= 68 else 1900 + yy
    try:
        return datetime(year, MONS[mon], dd, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_csv_list(value: str, token_re):
    """Strict CSV list parser; returns a list of tokens or None if malformed."""
    tokens = value.split(",")
    if all(token_re.match(t) for t in tokens):
        return tokens
    return None


def quarantine_doc(ns: str, cust_id: str, field: str, kind: str, reason: str,
                   raw_value) -> dict:
    return {
        "_id": mongo_common.det_id(ns, "quarantine", cust_id, kind, field),
        "ns": ns,
        "unit": UNIT,
        "source_table": SOURCE_TABLE,
        "source_pk": cust_id,
        "field": field,
        "kind": kind,
        "reason": reason,
        "raw_value": raw_value,
    }


def build_doc(ns: str, columns, row, attributes, quarantine_out) -> dict:
    raw = dict(zip(columns, row))
    cust_id = raw["cust_id"]
    doc = {"_id": mongo_common.det_id(ns, "customer", cust_id)}
    for field, value in raw.items():
        if value is None:
            continue  # NULL/missing source values are omitted, never defaulted
        if field in DATE_FIELDS:
            parsed = parse_legacy_date(value)
            if parsed is None:
                quarantine_out.append(quarantine_doc(
                    ns, cust_id, field, QUARANTINE_KINDS[field],
                    f"unparseable DD-MON-YY date in {field.upper()}", value))
                continue
            doc[field] = parsed
        elif field in CSV_FIELDS:
            parsed = parse_csv_list(value, CSV_FIELDS[field])
            if parsed is None:
                quarantine_out.append(quarantine_doc(
                    ns, cust_id, field, QUARANTINE_KINDS[field],
                    f"malformed CSV list in {field.upper()}", value))
                continue
            doc[field] = parsed
        else:
            doc[field] = value
    folded = attributes.get(cust_id)
    if folded:
        doc["attributes"] = folded
    return doc


def load_attributes(cur, batch_no: int) -> dict:
    """ENTITY_ATTR_VALUE rows keyed by owning customer, ordered by eav_id."""
    cur.execute(
        """SELECT eav_id, entity_id, attr_name, attr_value, attr_type, created_dt
             FROM entity_attr_value
            WHERE entity_type = 'CUSTOMER'
              AND entity_id IN (SELECT cust_id FROM customer_master
                                 WHERE conversion_batch_no = :1)
            ORDER BY eav_id""",
        [batch_no])
    attributes: dict[str, list] = {}
    for eav_id, entity_id, name, value, attr_type, created_dt in cur:
        entry = {"eav_id": int(eav_id), "name": name}
        if value is not None:
            entry["value"] = value
        if attr_type is not None:
            entry["type"] = attr_type
        if created_dt is not None:
            entry["created_dt_raw"] = created_dt
        attributes.setdefault(entity_id, []).append(entry)
    return attributes


def migrate(ns: str) -> int:
    batch_no = mongo_common.oracle_batch_no(ns)
    conn = mongo_common.oracle_connect()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM customer_master WHERE conversion_batch_no = :1",
                [batch_no])
    total = cur.fetchone()[0]
    if total == 0:
        # Empty/absent source namespace: write nothing, leave prior output.
        print(f"[{UNIT}] ns={ns}: source namespace is empty; no-op")
        return 0

    attributes = load_attributes(cur, batch_no)
    client = mongo_common.mongo_client()
    customers = client[mongo_common.target_db_name(ns)]["customers"]
    quarantine = client[mongo_common.quarantine_db_name(ns)]["customers_quarantine"]

    cur.execute("SELECT * FROM customer_master WHERE conversion_batch_no = :1 "
                "ORDER BY cust_id", [batch_no])
    columns = [d[0].lower() for d in cur.description]
    migrated = quarantined_records = 0
    quarantine_ops_total = 0
    while True:
        rows = cur.fetchmany(BATCH_SIZE)
        if not rows:
            break
        doc_ops, q_docs = [], []
        for row in rows:
            try:
                doc = build_doc(ns, columns, row, attributes, q_docs)
            except (UnicodeDecodeError, UnicodeError) as exc:
                # Invalid bytes never fail open into a valid-looking document.
                cust_id = row[columns.index("cust_id")]
                q_docs.append(quarantine_doc(
                    ns, str(cust_id), "*", "invalid_utf8",
                    f"record does not decode as UTF-8: {exc}", None))
                quarantined_records += 1
                continue
            doc_ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
        if doc_ops:
            customers.bulk_write(doc_ops, ordered=False)
            migrated += len(doc_ops)
        if q_docs:
            quarantine.bulk_write(
                [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in q_docs],
                ordered=False)
            quarantine_ops_total += len(q_docs)
    cur.close()
    conn.close()
    client.close()
    print(f"[{UNIT}] ns={ns}: migrated {migrated}/{total} documents, "
          f"{quarantine_ops_total} quarantine entries "
          f"({quarantined_records} whole records)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    args = ap.parse_args()
    return migrate(args.ns)


if __name__ == "__main__":
    sys.exit(main())

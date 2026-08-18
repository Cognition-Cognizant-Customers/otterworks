#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["oracledb==2.5.1", "pymongo==4.10.1"]
# ///
"""Migrate OW_BILLING.CUSTOMER_MASTER + ENTITY_ATTR_VALUE to MongoDB customers.

One document per customer: the 155-column master row collapses to only its
populated fields, EAV rows fold into an `attributes` subdocument, CSV list
columns become real arrays, and valid DD-MON-YY strings become BSON dates.
Malformed CSV lists and unparseable dates are quarantined with attribution
(source PK, field, raw value, reason) — never silently coerced; the customer
document still lands with the offending field omitted.

Targets: ow_tp_mongodb_<ns>.customers and
ow_tp_mongodb_<ns>_quarantine.customers_quarantine.

Deterministic and idempotent: all writes are upserts keyed by deterministic
ids (customer PK; uuid5 for quarantine records), batched per the contract's
per-batch granularity. A run against an empty/absent source namespace writes
nothing and leaves prior target output untouched. Run under
scripts/tp-run-deterministic.sh (TZ=UTC LC_ALL=C LANG=C).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import sys
import uuid

import oracledb
from pymongo import MongoClient, ReplaceOne

QUARANTINE_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "ow-tp-mongodb/customers-quarantine")
BATCH_SIZE = 5000

MONS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
DATE_RE = re.compile(r"^(\d{2})-([A-Z]{3})-(\d{2})$")
INT_LIST_RE = re.compile(r"^\d+$")

# Populated CUSTOMER_MASTER columns; the remaining legacy columns are NULL in
# the estate and are omitted from documents per the NULL policy, except
# CUST_NAME_UPPER, which is intentionally dropped (trigger-derived
# UPPER(cust_name), reproducible from `name`).
COLUMNS = [
    "CUST_ID", "TENANT_ID", "CUST_NO", "CUST_NAME", "LEGAL_NAME",
    "ADDR_LINE_1", "ADDR_LINE_2", "ADDR_LINE_3", "CITY", "STATE_CD", "ZIP",
    "PHONE1", "PHONE1_TYPE_CD", "PHONE2", "PHONE2_TYPE_CD", "EMAIL_1",
    "SIGNUP_DT", "LAST_ACTIVITY_DT", "STATUS_CD", "SUB_STATUS_CD",
    "CUST_TYPE_CD", "SEGMENT_CD", "REGION_CD", "TAX_EXEMPT_YN",
    "CREDIT_HOLD_YN", "VIP_YN", "CUR_BAL_AMT", "PAST_DUE_AMT",
    "YTD_BILLED_AMT", "CREDIT_LIMIT_AMT", "RELATED_ACCT_IDS",
    "PROMO_CODES_CSV", "LEGACY_SYS_KEY", "MAINFRAME_ACCT_NO",
    "CONVERSION_BATCH_NO", "CREATED_BY", "CREATED_DT", "UPDATED_BY",
    "UPDATED_DT", "CUST_SEQ_NO", "ROW_VERSION_NO",
]


def ns_batch_no(ns: str) -> int:
    """Mirror of the seeder's namespace batch derivation (legacy_common.ns_seed)."""
    seed = int(hashlib.sha256(ns.encode()).hexdigest()[:8], 16)
    return seed % 90_000_000 + 1_000_000


def parse_legacy_date(raw: str) -> datetime.datetime | None:
    """Strict DD-MON-YY -> datetime (UTC midnight); None when not a real date."""
    m = DATE_RE.match(raw)
    if not m:
        return None
    day, mon, yy = int(m.group(1)), m.group(2), int(m.group(3))
    month = MONS.get(mon)
    if month is None:
        return None
    year = 2000 + yy if yy <= 49 else 1900 + yy
    try:
        return datetime.datetime(year, month, day, tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def parse_csv_list(raw: str, token_re: re.Pattern) -> list[str] | None:
    """Strict CSV -> array; None when the list is malformed (never coerced)."""
    if raw == "":
        return []
    tokens = raw.split(",")
    if all(token_re.fullmatch(t) for t in tokens):
        return tokens
    return None


def quarantine_doc(ns: str, cust_id: str, field: str, raw, reason: str, kind: str,
                   source_table: str = "OW_BILLING.CUSTOMER_MASTER",
                   id_suffix: str = "") -> dict:
    qid = str(uuid.uuid5(QUARANTINE_NAMESPACE, f"{ns}:{cust_id}:{field}{id_suffix}"))
    return {
        "_id": qid,
        "ns": ns,
        "source_table": source_table,
        "source_pk": cust_id,
        "field": field,
        "raw_value": raw,
        "reason": reason,
        "anomaly_kind": kind,
    }


def ensure_str(value, ns: str, cust_id: str, field: str, quarantine: list) -> str | None:
    """Byte-transparency guard: values must round-trip as UTF-8 text."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            quarantine.append(quarantine_doc(
                ns, cust_id, field, value.hex(),
                "value does not decode as UTF-8", "invalid_encoding"))
            return None
    return value


def transform_row(ns: str, row: dict, eav: dict[str, list]) -> tuple[dict, list[dict]]:
    """One CUSTOMER_MASTER row (+ its EAV rows) -> (document, quarantine docs)."""
    cust_id = row["CUST_ID"]
    quarantine: list[dict] = []
    doc: dict = {"_id": cust_id, "ns": ns}

    def put(key: str, value) -> None:
        if value is not None and value != "":
            doc[key] = value

    put("tenant_id", row["TENANT_ID"])
    put("cust_no", row["CUST_NO"])
    put("name", ensure_str(row["CUST_NAME"], ns, cust_id, "CUST_NAME", quarantine))
    put("legal_name", ensure_str(row["LEGAL_NAME"], ns, cust_id, "LEGAL_NAME", quarantine))

    address = {k: v for k, v in {
        "line1": row["ADDR_LINE_1"], "line2": row["ADDR_LINE_2"],
        "line3": row["ADDR_LINE_3"], "city": row["CITY"],
        "state": row["STATE_CD"], "zip": row["ZIP"],
    }.items() if v is not None}
    if address:
        doc["address"] = address

    phones = []
    for num_col, type_col in (("PHONE1", "PHONE1_TYPE_CD"), ("PHONE2", "PHONE2_TYPE_CD")):
        if row[num_col] is not None:
            phone = {"number": row[num_col]}
            if row[type_col] is not None:
                phone["type_cd"] = int(row[type_col])
            phones.append(phone)
    if phones:
        doc["phones"] = phones
    put("email", row["EMAIL_1"])

    for src, dst in (("SIGNUP_DT", "signup_dt"), ("LAST_ACTIVITY_DT", "last_activity_dt")):
        raw = row[src]
        if raw is None:
            continue
        parsed = parse_legacy_date(raw)
        if parsed is None:
            quarantine.append(quarantine_doc(
                ns, cust_id, src, raw,
                f"not a valid DD-MON-YY date: {raw!r}", "dirty_dates"))
        else:
            doc[dst] = parsed

    status = {k: int(v) for k, v in {
        "status_cd": row["STATUS_CD"], "sub_status_cd": row["SUB_STATUS_CD"],
        "cust_type_cd": row["CUST_TYPE_CD"], "segment_cd": row["SEGMENT_CD"],
        "region_cd": row["REGION_CD"],
    }.items() if v is not None}
    if status:
        doc["status"] = status

    flags = {k: v == "Y" for k, v in {
        "tax_exempt": row["TAX_EXEMPT_YN"], "credit_hold": row["CREDIT_HOLD_YN"],
        "vip": row["VIP_YN"],
    }.items() if v is not None}
    if flags:
        doc["flags"] = flags

    balances = {k: float(v) for k, v in {
        "current": row["CUR_BAL_AMT"], "past_due": row["PAST_DUE_AMT"],
        "ytd_billed": row["YTD_BILLED_AMT"], "credit_limit": row["CREDIT_LIMIT_AMT"],
    }.items() if v is not None}
    if balances:
        doc["balances"] = balances

    related_raw = row["RELATED_ACCT_IDS"]
    if related_raw is not None:
        related = parse_csv_list(related_raw, INT_LIST_RE)
        if related is None:
            quarantine.append(quarantine_doc(
                ns, cust_id, "RELATED_ACCT_IDS", related_raw,
                f"malformed CSV list: {related_raw!r}", "malformed_csv_lists"))
        else:
            doc["related_acct_ids"] = related

    promos_raw = row["PROMO_CODES_CSV"]
    if promos_raw is not None:
        promos = parse_csv_list(promos_raw, re.compile(r"^[A-Za-z0-9]+$"))
        if promos is None:
            quarantine.append(quarantine_doc(
                ns, cust_id, "PROMO_CODES_CSV", promos_raw,
                f"malformed CSV list: {promos_raw!r}", "malformed_csv_lists"))
        else:
            doc["promo_codes"] = promos

    lineage = {k: v for k, v in {
        "legacy_sys_key": row["LEGACY_SYS_KEY"],
        "mainframe_acct_no": row["MAINFRAME_ACCT_NO"],
        "conversion_batch_no": int(row["CONVERSION_BATCH_NO"]),
        "cust_seq_no": int(row["CUST_SEQ_NO"]) if row["CUST_SEQ_NO"] is not None else None,
        "row_version_no": int(row["ROW_VERSION_NO"]) if row["ROW_VERSION_NO"] is not None else None,
        "created_by": row["CREATED_BY"], "created_dt": row["CREATED_DT"],
        "updated_by": row["UPDATED_BY"], "updated_dt": row["UPDATED_DT"],
    }.items() if v is not None}
    doc["lineage"] = lineage

    attributes: dict[str, list] = {}
    for eav_id, attr_name, attr_value, attr_type, created_raw in eav.get(cust_id, []):
        entry: dict = {}
        if attr_type is not None:
            entry["type"] = attr_type
        if attr_value is not None:
            entry["value"] = attr_value
        created = parse_legacy_date(created_raw) if created_raw else None
        if created is not None:
            entry["recorded_dt"] = created
        elif created_raw:
            quarantine.append(quarantine_doc(
                ns, cust_id, f"CREATED_DT[{attr_name}]", created_raw,
                f"not a valid DD-MON-YY date: {created_raw!r}", "dirty_dates",
                source_table="OW_BILLING.ENTITY_ATTR_VALUE",
                id_suffix=f"#{eav_id}"))
        attributes.setdefault(attr_name, []).append(entry)
    if attributes:
        doc["attributes"] = attributes

    return doc, quarantine


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", required=True)
    ap.add_argument("--mongodb-uri",
                    default=os.environ.get("MONGODB_URI", "mongodb://localhost:27017"))
    ap.add_argument("--oracle-host", default=os.environ.get("DB_HOST", "localhost"))
    ap.add_argument("--oracle-port", type=int,
                    default=int(os.environ.get("DB_PORT", "52521")))
    ap.add_argument("--oracle-user", default=os.environ.get("DB_USER", "ow_billing"))
    ap.add_argument("--oracle-password",
                    default=os.environ.get("DB_PASSWORD", "ow_billing"))
    ap.add_argument("--oracle-service", default=os.environ.get("DB_SERVICE", "FREEPDB1"))
    args = ap.parse_args()
    ns = args.ns
    if not re.fullmatch(r"[A-Za-z0-9_]+", ns):
        print("NS must match ^[A-Za-z0-9_]+$", file=sys.stderr)
        return 2
    batch_no = ns_batch_no(ns)

    conn = oracledb.connect(user=args.oracle_user, password=args.oracle_password,
                            host=args.oracle_host, port=args.oracle_port,
                            service_name=args.oracle_service)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM customer_master WHERE conversion_batch_no = :1",
                [batch_no])
    n_source = cur.fetchone()[0]
    if n_source == 0:
        # Empty-input semantics: no-op; prior target output untouched.
        print(f"[migrate] ns={ns}: source namespace empty; nothing written")
        return 0

    cur.execute("""SELECT eav.eav_id, eav.entity_id, eav.attr_name, eav.attr_value,
                          eav.attr_type, eav.created_dt
                     FROM entity_attr_value eav
                     JOIN customer_master cm ON cm.cust_id = eav.entity_id
                    WHERE eav.entity_type = 'CUSTOMER'
                      AND cm.conversion_batch_no = :1
                    ORDER BY eav.eav_id""", [batch_no])
    eav: dict[str, list] = {}
    n_eav = 0
    for eav_id, entity_id, attr_name, attr_value, attr_type, created_dt in cur:
        eav.setdefault(entity_id, []).append(
            (eav_id, attr_name, attr_value, attr_type, created_dt))
        n_eav += 1

    client = MongoClient(args.mongodb_uri)
    customers = client[f"ow_tp_mongodb_{ns}"]["customers"]
    quarantine = client[f"ow_tp_mongodb_{ns}_quarantine"]["customers_quarantine"]

    cur.execute(f"""SELECT {", ".join(COLUMNS)} FROM customer_master
                    WHERE conversion_batch_no = :1 ORDER BY cust_id""", [batch_no])
    n_docs = n_quar = 0
    # Source-side non-NULL counts, persisted so recon can compare target shape
    # against source expectations instead of the migration's own output.
    src_counts = {"RELATED_ACCT_IDS": 0, "PROMO_CODES_CSV": 0,
                  "SIGNUP_DT": 0, "LAST_ACTIVITY_DT": 0}
    doc_ops: list[ReplaceOne] = []
    quar_ops: list[ReplaceOne] = []
    while True:
        rows = cur.fetchmany(BATCH_SIZE)
        if not rows:
            break
        for values in rows:
            row = dict(zip(COLUMNS, values))
            for col in src_counts:
                if row[col] is not None:
                    src_counts[col] += 1
            doc, quar = transform_row(ns, row, eav)
            doc_ops.append(ReplaceOne({"_id": doc["_id"]}, doc, upsert=True))
            for q in quar:
                quar_ops.append(ReplaceOne({"_id": q["_id"]}, q, upsert=True))
        # Per-batch granularity: one bulk write per source fetch batch.
        customers.bulk_write(doc_ops, ordered=False)
        n_docs += len(doc_ops)
        doc_ops = []
        if quar_ops:
            quarantine.bulk_write(quar_ops, ordered=False)
            n_quar += len(quar_ops)
            quar_ops = []

    client[f"ow_tp_mongodb_{ns}"]["customers_migration_meta"].replace_one(
        {"_id": ns},
        {"_id": ns, "ns": ns, "source_nonnull_counts": src_counts},
        upsert=True)

    customers.create_index("tenant_id")
    customers.create_index("cust_no", unique=True, sparse=True)
    quarantine.create_index([("ns", 1), ("anomaly_kind", 1)])

    print(f"[migrate] ns={ns} customers={n_docs} eav_rows={n_eav} quarantined={n_quar}")
    cur.close()
    conn.close()
    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

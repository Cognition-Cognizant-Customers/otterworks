"""$jsonSchema validators for the migrated MongoDB showcase collections.

Derived from the unit contracts (docs/tech-partnerships/contracts/
mongo_customers.json, mongo_invoices.json) and the migrated document shape:
the legacy 155-column CUSTOMER_MASTER + EAV free-for-all becomes a closed,
typed document contract the database itself enforces.

Design notes, keyed to the legacy horrors each rule retires:
  - additionalProperties: false — no 156th ad-hoc column, ever. The legacy
    table grew UDF_/FLAG_ columns for a decade; here an unknown field is a
    write error.
  - signup_dt / last_activity_dt must be BSON dates — the legacy store kept
    VARCHAR2 DD-MON-YY strings; the 50 dirty ones are quarantined upstream
    and never stored, so the validator can demand real dates.
  - related_acct_ids / promo_codes are arrays of strings — formerly CSV
    blobs (RELATED_ACCT_IDS VARCHAR2(2000), PROMO_CODES_CSV) parsed by every
    consumer separately.
  - invoices carry their lines embedded, and the array is bounded — the
    orphaned lines the legacy schema allowed (37 in the demo seed) cannot
    be attached here; they live in the quarantine database.
  - invoice_dt / due_dt stay byte-transparent legacy DD-MON-YY strings per
    the mongo_invoices contract (checksum transparency), but the validator
    pins the format so nothing new and worse can creep in.
"""

from __future__ import annotations

DD_MON_YY = "^[0-9]{2}-[A-Z]{3}-[0-9]{2}$"

CUSTOMERS_SCHEMA: dict = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "_id", "ns", "tenant_id", "cust_no", "name", "legal_name", "email",
        "address", "phones", "status", "flags", "balances", "lineage",
        "last_activity_dt",
    ],
    "properties": {
        "_id": {"bsonType": "string"},
        "ns": {"bsonType": "string"},
        "tenant_id": {"bsonType": "string"},
        "cust_no": {"bsonType": "string"},
        "name": {"bsonType": "string"},
        "legal_name": {"bsonType": "string"},
        "email": {"bsonType": "string"},
        "address": {"bsonType": "object"},
        "phones": {"bsonType": "array", "items": {"bsonType": "object"}},
        "status": {"bsonType": "object"},
        "flags": {"bsonType": "object"},
        "balances": {"bsonType": "object"},
        "lineage": {"bsonType": "object"},
        "signup_dt": {
            "bsonType": "date",
            "description": "BSON date; legacy DD-MON-YY strings are quarantined upstream, never stored",
        },
        "last_activity_dt": {"bsonType": "date"},
        "related_acct_ids": {
            "bsonType": "array",
            "items": {"bsonType": "string"},
            "description": "array of account ids; formerly the RELATED_ACCT_IDS CSV blob",
        },
        "promo_codes": {
            "bsonType": "array",
            "items": {"bsonType": "string"},
            "description": "array of codes; formerly the PROMO_CODES_CSV blob",
        },
        "attributes": {
            "bsonType": "object",
            "description": "folded ENTITY_ATTR_VALUE rows, keyed by attribute name",
        },
    },
}

INVOICE_LINE_SCHEMA: dict = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "line_id", "line_no", "line_type_cd", "item_desc", "qty",
        "unit_price", "amount", "tax_amt", "invoice_dt", "service_period",
        "src_system", "gl_accts", "cust_no", "cust_name",
    ],
    "properties": {
        "line_id": {"bsonType": "string"},
        "line_no": {"bsonType": "decimal"},
        "line_type_cd": {"bsonType": "decimal"},
        "item_desc": {"bsonType": "string"},
        "qty": {"bsonType": "decimal"},
        "unit_price": {"bsonType": "decimal"},
        "amount": {"bsonType": "decimal"},
        "tax_amt": {"bsonType": "decimal"},
        "invoice_dt": {"bsonType": "string", "pattern": DD_MON_YY},
        "service_period": {"bsonType": "string"},
        "src_system": {"bsonType": "string"},
        "posted_yn": {"enum": ["Y", "N"]},
        "gl_accts": {
            "bsonType": "array",
            "items": {"bsonType": "string"},
            "description": "array of GL accounts; formerly the GL_ACCT_CSV blob",
        },
        "cust_no": {"bsonType": "string"},
        "cust_name": {"bsonType": "string"},
    },
}

INVOICES_SCHEMA: dict = {
    "bsonType": "object",
    "additionalProperties": False,
    "required": [
        "_id", "ns", "tenant_id", "cust_id", "invoice_no", "invoice_dt",
        "due_dt", "status_cd", "total_amt", "batch_no", "lines",
    ],
    "properties": {
        "_id": {"bsonType": "string"},
        "ns": {"bsonType": "string"},
        "tenant_id": {"bsonType": "string"},
        "cust_id": {"bsonType": "string"},
        "invoice_no": {"bsonType": "string"},
        "invoice_dt": {
            "bsonType": "string",
            "pattern": DD_MON_YY,
            "description": "byte-transparent legacy DD-MON-YY string per the mongo_invoices contract",
        },
        "due_dt": {"bsonType": "string", "pattern": DD_MON_YY},
        "status_cd": {"bsonType": "decimal"},
        "total_amt": {"bsonType": "decimal"},
        "batch_no": {"bsonType": "decimal"},
        "lines": {
            "bsonType": "array",
            "maxItems": 500,
            "items": INVOICE_LINE_SCHEMA,
            "description": "embedded invoice lines; orphans live in the quarantine database, never here",
        },
    },
}

VALIDATORS: dict[str, dict] = {
    "customers": CUSTOMERS_SCHEMA,
    "invoices": INVOICES_SCHEMA,
}

#!/usr/bin/env python3
"""Shared model for the ``mongo_customers`` unit.

Holds everything the migration and the recon script must agree on: the Oracle
source projection, the strict parsers for the legacy string encodings, the
document builder, and the ``$jsonSchema`` validators.

Design rules that come from the unit contract:

* A sparse relational column becomes an **optional field**. A NULL or missing
  source value is never written as ``null`` and never defaulted, so a reader
  can tell "no value in the legacy system" from "value present" (contract
  ``null_attribution: fail`` — NULL must not fail open into a valid-looking
  document).
* ``DD-MON-YY`` strings become BSON dates only when they denote a real
  calendar day; anything else is an anomaly: the field is left **absent** on
  the customer document and the record is attributed into
  ``customers_quarantine`` with its raw bytes intact (contract
  ``malformed_record_policy: tolerate-and-attribute``). A dirty date can never
  become a plausible date.
* CSV columns become BSON arrays only when every delimiter-separated token is
  a non-empty bare integer; otherwise the field is absent and the record is
  attributed to quarantine, never truncated or repaired.
* ``ENTITY_ATTR_VALUE`` rows fold into a typed ``attributes`` subdocument, so
  reading a customer needs no join. Repeated ``(customer, attr_name)`` rows
  keep the newest value in ``attributes``; ``attributes_entries`` carries every
  source row, so no EAV row is dropped by the fold.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from decimal import Decimal
from typing import Any

from bson.decimal128 import Decimal128

SCHEMA_VERSION = 1

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

ANOMALY_DIRTY_SIGNUP_DT = "dirty_signup_dt"
ANOMALY_MALFORMED_RELATED_ACCT_IDS = "malformed_related_acct_ids"

# Legacy denormalizations that are retired rather than migrated: CUST_NAME_UPPER
# is a trigger-maintained copy of CUST_NAME and is recomputable on read.
RETIRED_COLUMNS = ("CUST_NAME_UPPER",)

DATE_COLUMNS = {
    "SIGNUP_DT": "signup_date",
    "LAST_ACTIVITY_DT": "last_activity_date",
    "LAST_INVOICE_DT": "last_invoice_date",
    "LAST_PAYMENT_DT": "last_payment_date",
    "TERMINATE_DT": "terminate_date",
}
# CSV list columns become BSON arrays of strings: the source tokens are carried
# through byte-for-byte (contract ``byte_transparency``) while still being
# validated as bare integers, so a malformed list is detectable but a well formed
# one is not renumbered or re-padded.
CSV_LIST_COLUMNS = {
    "RELATED_ACCT_IDS": "related_acct_ids",
    "CHILD_ACCT_IDS": "child_acct_ids",
}
CSV_CODE_COLUMNS = {"PROMO_CODES_CSV": "promo_codes"}
AMOUNT_COLUMNS = {
    "CUR_BAL_AMT": "balances.current",
    "PAST_DUE_AMT": "balances.past_due",
    "YTD_BILLED_AMT": "balances.ytd_billed",
    "LTD_BILLED_AMT": "balances.ltd_billed",
    "YTD_PAID_AMT": "balances.ytd_paid",
    "CREDIT_LIMIT_AMT": "balances.credit_limit",
}
CODE_COLUMNS = {
    "STATUS_CD": "codes.status", "SUB_STATUS_CD": "codes.sub_status",
    "CUST_TYPE_CD": "codes.cust_type", "SEGMENT_CD": "codes.segment",
    "REGION_CD": "codes.region", "TERRITORY_CD": "codes.territory",
    "CHANNEL_CD": "codes.channel", "RATE_CLASS_CD": "codes.rate_class",
}
YN_COLUMNS = {
    "TAX_EXEMPT_YN": "flags.tax_exempt", "CREDIT_HOLD_YN": "flags.credit_hold",
    "DUNNING_EXEMPT_YN": "flags.dunning_exempt", "VIP_YN": "flags.vip",
}
# Whole-number legacy columns: Oracle NUMBER is fetched as Decimal, so these are
# narrowed to BSON integers rather than stored as decimals.
INT_COLUMNS = {
    "CONVERSION_BATCH_NO": "legacy.conversion_batch_no",
    "CUST_SEQ_NO": "legacy.cust_seq_no",
    "ROW_VERSION_NO": "audit.row_version",
}
SCALAR_COLUMNS = {
    "TENANT_ID": "tenant_id", "CUST_NO": "cust_no", "CUST_NAME": "name",
    "LEGAL_NAME": "legal_name", "DBA_NAME": "dba_name",
    "CITY": "address.city", "STATE_CD": "address.state",
    "ZIP": "address.zip", "ZIP4": "address.zip4",
    "COUNTRY_CD": "address.country",
    "MAIL_CITY": "mail_address.city", "MAIL_STATE_CD": "mail_address.state",
    "MAIL_ZIP": "mail_address.zip",
    "FAX": "contacts.fax", "CONTACT_NOTES": "contact_notes",
    "LEGACY_SYS_KEY": "legacy.sys_key",
    "MAINFRAME_ACCT_NO": "legacy.mainframe_acct_no",
    "CREATED_BY": "audit.created_by", "CREATED_DT": "audit.created_at",
    "UPDATED_BY": "audit.updated_by", "UPDATED_DT": "audit.updated_at",
}
for _i in range(1, 7):
    SCALAR_COLUMNS[f"ADDR_LINE_{_i}"] = f"address.line{_i}"
    SCALAR_COLUMNS[f"MAIL_ADDR_LINE_{_i}"] = f"mail_address.line{_i}"
for _i in range(1, 21):
    YN_COLUMNS[f"FLAG_{_i:02d}"] = f"flags.legacy.flag_{_i:02d}"
for _i in range(1, 41):
    SCALAR_COLUMNS[f"UDF_{_i:02d}"] = f"udf.text_{_i:02d}"
for _i in range(1, 11):
    AMOUNT_COLUMNS[f"UDF_AMT_{_i:02d}"] = f"udf.amount_{_i:02d}"
    DATE_COLUMNS[f"UDF_DT_{_i:02d}"] = f"udf.date_{_i:02d}"

PHONE_SLOTS = (1, 2, 3, 4)
EMAIL_SLOTS = (1, 2, 3)


class AnomalyError(Exception):
    """A source value that must be quarantined rather than converted."""

    def __init__(self, anomaly_id: str, column: str, raw: Any, reason: str):
        super().__init__(f"{anomaly_id}: {column}={raw!r} ({reason})")
        self.anomaly_id = anomaly_id
        self.column = column
        self.raw = raw
        self.reason = reason


def parse_legacy_date(raw: str, column: str, anomaly_id: str) -> _dt.datetime:
    """Parse a legacy ``DD-MON-YY`` string into a BSON-ready datetime.

    Strict on purpose: a value that is not an existing calendar day (``31-FEB-24``)
    or not the expected shape at all (``N/A``) raises instead of being coerced,
    so a dirty date can never become a plausible date. Two-digit years follow
    the estate's Oracle ``RR`` convention: ``00``-``69`` are 2000s, the rest 1900s.
    """
    if not isinstance(raw, str) or not re.fullmatch(r"\d{2}-[A-Z]{3}-\d{2}", raw):
        raise AnomalyError(anomaly_id, column, raw, "not a DD-MON-YY value")
    day_s, mon_s, yy_s = raw.split("-")
    if mon_s not in MONTHS:
        raise AnomalyError(anomaly_id, column, raw, "unknown month abbreviation")
    yy = int(yy_s)
    year = 2000 + yy if yy <= 69 else 1900 + yy
    try:
        return _dt.datetime(year, MONTHS.index(mon_s) + 1, int(day_s))
    except ValueError:
        raise AnomalyError(anomaly_id, column, raw, "not a real calendar day") from None


def parse_csv_list(raw: str, column: str, anomaly_id: str) -> list[str]:
    """Parse a comma-separated account-id list into a real BSON array.

    Every token must be a non-empty bare run of digits. Empty tokens (``,,`` or a
    trailing comma), padded tokens, and non-numeric tokens make the whole list
    malformed: it is never partially accepted or truncated. Well formed tokens
    are kept as their source strings, so the account ids are byte-transparent.
    """
    if not isinstance(raw, str):
        raise AnomalyError(anomaly_id, column, raw, "not a string value")
    tokens = raw.split(",")
    for token in tokens:
        if not re.fullmatch(r"\d+", token):
            raise AnomalyError(anomaly_id, column, raw,
                               f"token {token!r} is not a bare integer")
    return tokens


def parse_csv_codes(raw: str, column: str, anomaly_id: str) -> list[str]:
    """Parse a comma-separated code list (``PROMO_CODES_CSV``) into an array."""
    if not isinstance(raw, str):
        raise AnomalyError(anomaly_id, column, raw, "not a string value")
    tokens = raw.split(",")
    for token in tokens:
        if not re.fullmatch(r"[A-Z0-9_]+", token):
            raise AnomalyError(anomaly_id, column, raw,
                               f"token {token!r} is not a bare code")
    return tokens


def parse_yn(raw: str, column: str) -> bool:
    value = raw.strip() if isinstance(raw, str) else raw
    if value == "Y":
        return True
    if value == "N":
        return False
    raise AnomalyError(f"unexpected_{column.lower()}", column, raw,
                       "not a Y/N indicator")


def type_attr_value(raw: str) -> bool | int | float | str:
    """Type an EAV string value.

    The legacy EAV column is a single ``VARCHAR2`` bucket for booleans, numbers
    and free text, so typing is value-directed and lossless in round-trip terms:
    ``Y``/``TRUE`` and ``N``/``FALSE`` become booleans, bare integers and
    decimals become numbers, everything else stays a string. The raw string is
    preserved in ``attributes_raw`` so no byte is lost.
    """
    text = raw.strip() if isinstance(raw, str) else raw
    if text in ("Y", "TRUE"):
        return True
    if text in ("N", "FALSE"):
        return False
    if re.fullmatch(r"-?\d+", text or ""):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text or ""):
        return float(text)
    return raw


def _assign(doc: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    node = doc
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _money(value: Decimal | float | int) -> Decimal128:
    return Decimal128(value if isinstance(value, Decimal) else Decimal(str(value)))


def build_customer(row: dict[str, Any], attributes: list[dict[str, Any]],
                   ns: str) -> tuple[dict[str, Any], list[AnomalyError]]:
    """Build the target document for one ``CUSTOMER_MASTER`` row.

    Returns the document plus every anomaly found in it. An anomalous value is
    never converted: its field is omitted from the document (absent, not a
    coerced or null-filled stand-in) and the returned anomalies drive the
    quarantine record, so a reader can never mistake a dirty source value for a
    migrated one.
    """
    doc: dict[str, Any] = {
        "_id": row["CUST_ID"],
        "ns": ns,
        "schema_version": SCHEMA_VERSION,
    }
    for column, path in SCALAR_COLUMNS.items():
        value = row.get(column)
        if value is None:
            continue  # sparse column: field is absent, never null-filled
        _assign(doc, path, value)
    for column, path in list(CODE_COLUMNS.items()) + list(INT_COLUMNS.items()):
        value = row.get(column)
        if value is not None:
            _assign(doc, path, int(value))
    for column, path in AMOUNT_COLUMNS.items():
        value = row.get(column)
        if value is not None:
            _assign(doc, path, _money(value))
    for column, path in YN_COLUMNS.items():
        value = row.get(column)
        if value is not None:
            _assign(doc, path, parse_yn(value, column))
    anomalies: list[AnomalyError] = []
    for column, path in DATE_COLUMNS.items():
        value = row.get(column)
        if value is None:
            continue
        anomaly = (ANOMALY_DIRTY_SIGNUP_DT if column == "SIGNUP_DT"
                   else f"dirty_{column.lower()}")
        try:
            _assign(doc, path, parse_legacy_date(value, column, anomaly))
        except AnomalyError as err:
            anomalies.append(err)
    for column, path in CSV_LIST_COLUMNS.items():
        value = row.get(column)
        if value is None:
            continue
        anomaly = (ANOMALY_MALFORMED_RELATED_ACCT_IDS
                   if column == "RELATED_ACCT_IDS" else f"malformed_{column.lower()}")
        try:
            _assign(doc, path, parse_csv_list(value, column, anomaly))
        except AnomalyError as err:
            anomalies.append(err)
    for column, path in CSV_CODE_COLUMNS.items():
        value = row.get(column)
        if value is None:
            continue
        try:
            _assign(doc, path, parse_csv_codes(value, column,
                                               f"malformed_{column.lower()}"))
        except AnomalyError as err:
            anomalies.append(err)

    phones = []
    for slot in PHONE_SLOTS:
        number = row.get(f"PHONE{slot}")
        type_cd = row.get(f"PHONE{slot}_TYPE_CD")
        if number is None and type_cd is None:
            continue
        entry: dict[str, Any] = {"slot": slot}
        if number is not None:
            entry["number"] = number
        if type_cd is not None:
            entry["type_cd"] = int(type_cd)
        phones.append(entry)
    if phones:
        _assign(doc, "contacts.phones", phones)
    emails = [{"slot": slot, "address": row[f"EMAIL_{slot}"]}
              for slot in EMAIL_SLOTS if row.get(f"EMAIL_{slot}") is not None]
    if emails:
        _assign(doc, "contacts.emails", emails)

    typed: dict[str, Any] = {}
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    # attributes: newest row (highest EAV_ID) per attribute name, typed, so a
    # customer reads with no join. attributes_entries: every source EAV row, so
    # the fold drops nothing even when an attribute repeats.
    for attr in sorted(attributes, key=lambda a: int(a["EAV_ID"]), reverse=True):
        key = attr["ATTR_NAME"].lower()
        value = type_attr_value(attr["ATTR_VALUE"])
        entries.append({
            "name": key,
            "eav_id": int(attr["EAV_ID"]),
            "attr_type": attr["ATTR_TYPE"],
            "raw_value": attr["ATTR_VALUE"],
            "value": value,
            "current": key not in seen,
        })
        if key not in seen:
            typed[key] = value
            seen.add(key)
    if typed:
        doc["attributes"] = typed
        doc["attributes_entries"] = sorted(entries, key=lambda e: e["eav_id"])
    if anomalies:
        # Attribution marker: this record is also enumerated in quarantine.
        doc["quarantined"] = True
        doc["quarantine_anomaly_ids"] = sorted({a.anomaly_id for a in anomalies})
    return doc, anomalies


def build_quarantine(row: dict[str, Any], attributes: list[dict[str, Any]],
                     ns: str, anomalies: list[AnomalyError]) -> dict[str, Any]:
    """Build the quarantine document for a record that must not be converted."""
    return {
        "_id": f"CUSTOMER_MASTER:{row['CUST_ID']}",
        "ns": ns,
        "schema_version": SCHEMA_VERSION,
        "source": {
            "system": "oracle",
            "schema": "OW_BILLING",
            "table": "CUSTOMER_MASTER",
            "primary_key": {"column": "CUST_ID", "value": row["CUST_ID"]},
            "cust_no": row.get("CUST_NO"),
        },
        "anomalies": [
            {
                "anomaly_id": a.anomaly_id,
                "column": a.column,
                "raw_value": a.raw,
                "reason": a.reason,
            }
            for a in sorted(anomalies, key=lambda a: (a.anomaly_id, a.column))
        ],
        # Raw source bytes, verbatim: nothing is coerced, repaired or truncated.
        "raw_record": {k: (str(v) if isinstance(v, Decimal) else v)
                       for k, v in row.items() if v is not None},
        "raw_attributes": [
            {"eav_id": int(a["EAV_ID"]), "attr_name": a["ATTR_NAME"],
             "attr_value": a["ATTR_VALUE"], "attr_type": a["ATTR_TYPE"]}
            for a in sorted(attributes, key=lambda a: a["EAV_ID"])
        ],
    }


def balance_checksum(pairs: list[tuple[str, str]]) -> str:
    """Reproduce the seed manifest's checksum: md5 of ``pk:amount`` in PK order."""
    digest = hashlib.md5()
    for pk, amount in sorted(pairs):
        digest.update(f"{pk}:{amount}\n".encode())
    return digest.hexdigest()


CUSTOMERS_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "ow_tp customers (mongo_customers unit)",
        # signup_date is deliberately NOT required: a dirty source date leaves
        # the field absent and quarantined rather than fabricating a value.
        "required": ["_id", "ns", "schema_version", "cust_no", "name",
                     "tenant_id"],
        "properties": {
            "_id": {"bsonType": "string", "pattern": "^[0-9a-f-]{36}$"},
            "ns": {"bsonType": "string", "minLength": 1},
            "schema_version": {"bsonType": "int", "minimum": 1},
            "cust_no": {"bsonType": "string", "minLength": 1},
            "name": {"bsonType": "string", "minLength": 1},
            "legal_name": {"bsonType": "string"},
            "dba_name": {"bsonType": "string"},
            "tenant_id": {"bsonType": "string", "minLength": 1},
            "contact_notes": {"bsonType": "string"},
            # Dirty legacy dates are quarantined, so when present this is a date.
            "signup_date": {"bsonType": "date"},
            "last_activity_date": {"bsonType": "date"},
            "last_invoice_date": {"bsonType": "date"},
            "last_payment_date": {"bsonType": "date"},
            "terminate_date": {"bsonType": "date"},
            # CSV strings became real BSON arrays.
            "related_acct_ids": {
                "bsonType": "array",
                "items": {"bsonType": "string", "pattern": "^[0-9]+$"},
            },
            "child_acct_ids": {
                "bsonType": "array",
                "items": {"bsonType": "string", "pattern": "^[0-9]+$"},
            },
            "promo_codes": {"bsonType": "array", "items": {"bsonType": "string"}},
            "address": {
                "bsonType": "object",
                "properties": {
                    "city": {"bsonType": "string"}, "state": {"bsonType": "string"},
                    "zip": {"bsonType": "string"}, "zip4": {"bsonType": "string"},
                    "country": {"bsonType": "string"},
                    **{f"line{i}": {"bsonType": "string"} for i in range(1, 7)},
                },
                "additionalProperties": False,
            },
            "mail_address": {
                "bsonType": "object",
                "properties": {
                    "city": {"bsonType": "string"}, "state": {"bsonType": "string"},
                    "zip": {"bsonType": "string"},
                    **{f"line{i}": {"bsonType": "string"} for i in range(1, 7)},
                },
                "additionalProperties": False,
            },
            "contacts": {
                "bsonType": "object",
                "properties": {
                    "fax": {"bsonType": "string"},
                    "phones": {
                        "bsonType": "array",
                        "items": {
                            "bsonType": "object",
                            "required": ["slot"],
                            "properties": {
                                "slot": {"bsonType": "int", "minimum": 1, "maximum": 4},
                                "number": {"bsonType": "string"},
                                "type_cd": {"bsonType": "int"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "emails": {
                        "bsonType": "array",
                        "items": {
                            "bsonType": "object",
                            "required": ["slot", "address"],
                            "properties": {
                                "slot": {"bsonType": "int", "minimum": 1, "maximum": 3},
                                "address": {"bsonType": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
            "codes": {
                "bsonType": "object",
                "properties": {name.split(".")[1]: {"bsonType": "int"}
                               for name in CODE_COLUMNS.values()},
                "additionalProperties": False,
            },
            "balances": {
                "bsonType": "object",
                "properties": {path.split(".")[1]: {"bsonType": "decimal"}
                               for col, path in AMOUNT_COLUMNS.items()
                               if path.startswith("balances.")},
                "additionalProperties": False,
            },
            "flags": {
                "bsonType": "object",
                "properties": {
                    **{path.split(".")[1]: {"bsonType": "bool"}
                       for path in YN_COLUMNS.values() if path.count(".") == 1},
                    "legacy": {
                        "bsonType": "object",
                        "properties": {f"flag_{i:02d}": {"bsonType": "bool"}
                                       for i in range(1, 21)},
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
            "udf": {
                "bsonType": "object",
                "properties": {
                    **{f"text_{i:02d}": {"bsonType": "string"} for i in range(1, 41)},
                    **{f"amount_{i:02d}": {"bsonType": "decimal"} for i in range(1, 11)},
                    **{f"date_{i:02d}": {"bsonType": "date"} for i in range(1, 11)},
                },
                "additionalProperties": False,
            },
            "legacy": {
                "bsonType": "object",
                "properties": {
                    "sys_key": {"bsonType": "string"},
                    "mainframe_acct_no": {"bsonType": "string"},
                    "conversion_batch_no": {"bsonType": ["int", "long"]},
                    "cust_seq_no": {"bsonType": ["int", "long"]},
                },
                "additionalProperties": False,
            },
            "audit": {
                "bsonType": "object",
                "properties": {
                    "created_by": {"bsonType": "string"},
                    "created_at": {"bsonType": "date"},
                    "updated_by": {"bsonType": "string"},
                    "updated_at": {"bsonType": "date"},
                    "row_version": {"bsonType": ["int", "long"]},
                },
                "additionalProperties": False,
            },
            # EAV rows folded per customer: no join needed to read a customer.
            "attributes": {"bsonType": "object", "minProperties": 1},
            "attributes_entries": {
                "bsonType": "array",
                "minItems": 1,
                "items": {
                    "bsonType": "object",
                    "required": ["name", "eav_id", "raw_value", "value", "current"],
                    "properties": {
                        "name": {"bsonType": "string"},
                        "eav_id": {"bsonType": ["int", "long"]},
                        "attr_type": {"bsonType": "string"},
                        "raw_value": {"bsonType": "string"},
                        "value": {"bsonType": ["string", "bool", "int", "double"]},
                        "current": {"bsonType": "bool"},
                    },
                    "additionalProperties": False,
                },
            },
            "quarantined": {"bsonType": "bool"},
            "quarantine_anomaly_ids": {
                "bsonType": "array",
                "minItems": 1,
                "items": {"bsonType": "string", "minLength": 1},
            },
        },
        "additionalProperties": False,
    }
}

QUARANTINE_VALIDATOR: dict[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "ow_tp customers_quarantine (mongo_customers unit)",
        "required": ["_id", "ns", "schema_version", "source", "anomalies",
                     "raw_record"],
        "properties": {
            "_id": {"bsonType": "string", "minLength": 1},
            "ns": {"bsonType": "string", "minLength": 1},
            "schema_version": {"bsonType": "int", "minimum": 1},
            "source": {
                "bsonType": "object",
                "required": ["system", "schema", "table", "primary_key"],
                "properties": {
                    "system": {"bsonType": "string"},
                    "schema": {"bsonType": "string"},
                    "table": {"bsonType": "string"},
                    "cust_no": {"bsonType": "string"},
                    "primary_key": {
                        "bsonType": "object",
                        "required": ["column", "value"],
                        "properties": {"column": {"bsonType": "string"},
                                       "value": {"bsonType": "string"}},
                        "additionalProperties": False,
                    },
                },
                "additionalProperties": False,
            },
            "anomalies": {
                "bsonType": "array",
                "minItems": 1,
                "items": {
                    "bsonType": "object",
                    "required": ["anomaly_id", "column", "raw_value", "reason"],
                    "properties": {
                        "anomaly_id": {"bsonType": "string", "minLength": 1},
                        "column": {"bsonType": "string", "minLength": 1},
                        "raw_value": {"bsonType": ["string", "null"]},
                        "reason": {"bsonType": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
            },
            "raw_record": {"bsonType": "object", "minProperties": 1},
            "raw_attributes": {"bsonType": "array"},
        },
        "additionalProperties": False,
    }
}

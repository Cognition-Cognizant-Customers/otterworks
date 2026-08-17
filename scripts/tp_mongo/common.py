#!/usr/bin/env python3
"""Shared local Mongo invoice migration helpers."""
from __future__ import annotations

import decimal
import json
import os
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import oracledb
from bson import Decimal128
from pymongo import MongoClient, ReplaceOne

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "testdata" / "legacy" / "manifests"
DATE_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
MONEY_QUANTUM = decimal.Decimal("0.01")

HEADER_COLUMNS = (
    "invoice_id",
    "invoice_no",
    "cust_id",
    "tenant_id",
    "invoice_dt",
    "due_dt",
    "status_cd",
    "total_amt",
)
LINE_COLUMNS = (
    "line_id",
    "invoice_no",
    "invoice_id",
    "cust_id",
    "cust_no",
    "cust_name",
    "tenant_id",
    "line_no",
    "line_type_cd",
    "item_desc",
    "qty",
    "unit_price",
    "amount",
    "tax_amt",
    "invoice_dt",
    "service_period",
    "posted_yn",
    "gl_acct_csv",
    "batch_no",
    "src_system",
)
SOURCE_ROW_COLUMNS = (
    "LINE_ID",
    "INVOICE_NO",
    "INVOICE_ID",
    "CUST_ID",
    "CUST_NO",
    "CUST_NAME",
    "TENANT_ID",
    "LINE_NO",
    "LINE_TYPE_CD",
    "ITEM_DESC",
    "QTY",
    "UNIT_PRICE",
    "AMOUNT",
    "TAX_AMT",
    "INVOICE_DT",
    "SERVICE_PERIOD",
    "POSTED_YN",
    "GL_ACCT_CSV",
    "BATCH_NO",
    "SRC_SYSTEM",
)


class DecodingError(ValueError):
    """A source value was not valid UTF-8."""

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        super().__init__("source value is not valid UTF-8")


class NullRequiredField(ValueError):
    """A required source field was NULL."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"{field} is NULL")


class UnparseableRequiredField(ValueError):
    """A required non-money source field could not be parsed."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"{field} is unparseable")


def validate_namespace(namespace: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]+", namespace):
        raise SystemExit("--ns must contain only letters, digits, and underscores")


def manifest_path(namespace: str) -> Path:
    return MANIFEST_DIR / f"{namespace}.json"


def load_manifest(namespace: str) -> dict[str, Any]:
    path = manifest_path(namespace)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise SystemExit(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"manifest is not valid JSON: {path}: {exc}") from exc


def resolve_batch(namespace: str, explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    manifest = load_manifest(namespace)
    try:
        return int(
            manifest["seed_legacy_params"][
                "oracle.OW_BILLING.INVOICE_LINE"
            ]["batch_no"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            "batch number is required: pass --batch-no or provide "
            "seed_legacy_params.oracle.OW_BILLING.INVOICE_LINE.batch_no "
            f"in {manifest_path(namespace)}"
        ) from exc


def oracle_connection(args: Any) -> oracledb.Connection:
    return oracledb.connect(
        user=args.oracle_user,
        password=args.oracle_password,
        host=args.oracle_host,
        port=args.oracle_port,
        service_name=args.oracle_service,
    )


def install_decimal_handler(cursor: oracledb.Cursor) -> None:
    def output_type_handler(
        cur: oracledb.Cursor, metadata: Any
    ) -> Any:
        if metadata.type_code is oracledb.DB_TYPE_NUMBER:
            return cur.var(decimal.Decimal, arraysize=cur.arraysize)
        return None

    cursor.outputtypehandler = output_type_handler


def decode_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise DecodingError(value) from exc
    if isinstance(value, str):
        return value
    return str(value)


def safe_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return f"0x{value.hex()}"
    return str(value)


def decimal_value(value: Any) -> decimal.Decimal | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError("float values are not accepted for money")
    if isinstance(value, decimal.Decimal):
        return value
    if isinstance(value, int):
        return decimal.Decimal(value)
    if isinstance(value, bytes):
        value = decode_text(value)
    return decimal.Decimal(value)


def decimal128(value: decimal.Decimal) -> Decimal128:
    return Decimal128(value)


def decimal128_or_none(value: decimal.Decimal | None) -> Decimal128 | None:
    return None if value is None else decimal128(value)


def decimal_text(value: Any) -> str:
    if isinstance(value, Decimal128):
        value = value.to_decimal()
    if not isinstance(value, decimal.Decimal):
        value = decimal_value(value)
    assert value is not None
    return format(value.quantize(MONEY_QUANTUM), "f")


def parse_legacy_date(raw_value: Any) -> tuple[datetime | None, str | None, bool]:
    raw = decode_text(raw_value)
    if raw is None:
        return None, None, True
    try:
        day_text, month_text, year_text = raw.split("-")
        if len(day_text) != 2 or len(month_text) != 3 or len(year_text) != 2:
            raise ValueError
        month = DATE_MONTHS[month_text.upper()]
        day = int(day_text)
        year_number = int(year_text)
        year = 2000 + year_number if year_number <= 68 else 1900 + year_number
        value = datetime(year, month, day, tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None, raw, True
    return value, raw, False


def mongo_database(args: Any) -> tuple[MongoClient, Any]:
    uri = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017")
    name = (args.mongo_db or f"ow_tp_{args.ns}").lower()
    client = MongoClient(uri)
    return client, client[name]


def source_descriptor(batch_no: int) -> dict[str, Any]:
    return {"system": "oracle", "schema": "OW_BILLING", "batch_no": batch_no}


def bulk_replace(collection: Any, operations: list[ReplaceOne]) -> None:
    if operations:
        collection.bulk_write(operations, ordered=False)
        operations.clear()


def chunked(values: Iterable[Any], size: int) -> Iterable[list[Any]]:
    chunk: list[Any] = []
    for value in values:
        chunk.append(value)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def source_validator() -> dict[str, Any]:
    return {
        "bsonType": "object",
        "required": ["system", "schema", "batch_no"],
        "additionalProperties": False,
        "properties": {
            "system": {"bsonType": "string"},
            "schema": {"bsonType": "string"},
            "batch_no": {"bsonType": ["int", "long"]},
        },
    }


def line_validator() -> dict[str, Any]:
    return {
        "bsonType": "object",
        "required": [
            "line_id",
            "line_no",
            "line_type_cd",
            "item_desc",
            "qty",
            "unit_price",
            "amount",
            "tax_amt",
            "invoice_dt",
            "invoice_dt_raw",
            "service_period",
            "posted",
            "gl_accts",
            "gl_acct_csv",
            "src_system",
            "cust_no",
            "cust_name",
        ],
        "additionalProperties": False,
        "properties": {
            "line_id": {"bsonType": "string"},
            "line_no": {"bsonType": ["int", "long"]},
            "line_type_cd": {"bsonType": ["int", "long"]},
            "item_desc": {"bsonType": ["string", "null"]},
            "qty": {"bsonType": "decimal"},
            "unit_price": {"bsonType": "decimal"},
            "amount": {"bsonType": "decimal"},
            "tax_amt": {"bsonType": "decimal"},
            "invoice_dt": {"bsonType": ["date", "null"]},
            "invoice_dt_raw": {"bsonType": ["string", "null"]},
            "service_period": {"bsonType": ["string", "null"]},
            "posted": {"bsonType": ["bool", "null"]},
            "gl_accts": {
                "bsonType": "array",
                "items": {"bsonType": "string"},
            },
            "gl_acct_csv": {"bsonType": ["string", "null"]},
            "src_system": {"bsonType": ["string", "null"]},
            "cust_no": {"bsonType": ["string", "null"]},
            "cust_name": {"bsonType": ["string", "null"]},
        },
    }


def invoices_validator() -> dict[str, Any]:
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "_id",
                "ns",
                "invoice_no",
                "cust_id",
                "tenant_id",
                "status_cd",
                "invoice_dt",
                "due_dt",
                "invoice_dt_raw",
                "due_dt_raw",
                "total_amt",
                "tax_amt",
                "legacy_total_amt",
                "legacy_total_matches_lines",
                "line_count",
                "lines",
                "source",
                "data_quality",
            ],
            "additionalProperties": False,
            "properties": {
                "_id": {"bsonType": "string"},
                "ns": {"bsonType": "string"},
                "invoice_no": {"bsonType": ["string", "null"]},
                "cust_id": {"bsonType": ["string", "null"]},
                "tenant_id": {"bsonType": ["string", "null"]},
                "status_cd": {"bsonType": ["int", "long"]},
                "invoice_dt": {"bsonType": ["date", "null"]},
                "due_dt": {"bsonType": ["date", "null"]},
                "invoice_dt_raw": {"bsonType": ["string", "null"]},
                "due_dt_raw": {"bsonType": ["string", "null"]},
                "total_amt": {"bsonType": "decimal"},
                "tax_amt": {"bsonType": "decimal"},
                "legacy_total_amt": {"bsonType": "decimal"},
                "legacy_total_matches_lines": {"bsonType": "bool"},
                "line_count": {"bsonType": ["int", "long"]},
                "lines": {"bsonType": "array", "items": line_validator()},
                "source": source_validator(),
                "data_quality": {
                    "bsonType": "array",
                    "items": {"bsonType": "string"},
                },
            },
        }
    }


def quarantine_validator() -> dict[str, Any]:
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "required": [
                "_id",
                "ns",
                "anomaly_id",
                "reason",
                "invoice_id",
                "line_id",
                "invoice_no",
                "cust_id",
                "line_no",
                "amount",
                "source_row",
                "source",
            ],
            "additionalProperties": False,
            "properties": {
                "_id": {"bsonType": "string"},
                "ns": {"bsonType": "string"},
                "anomaly_id": {
                    "enum": [
                        "orphaned_rows",
                        "null_invoice_id",
                        "null_amount",
                        "unparseable_amount",
                        "invalid_encoding",
                        "null_required_field",
                    ]
                },
                "reason": {"bsonType": "string"},
                "invoice_id": {"bsonType": ["string", "null"]},
                "line_id": {"bsonType": "string"},
                "invoice_no": {"bsonType": ["string", "null"]},
                "cust_id": {"bsonType": ["string", "null"]},
                "line_no": {"bsonType": ["int", "long", "null"]},
                "amount": {"bsonType": ["decimal", "null"]},
                "source_row": {"bsonType": "object"},
                "source": source_validator(),
            },
        }
    }


def ensure_collections(database: Any) -> None:
    definitions = {
        "invoices": invoices_validator(),
        "invoices_quarantine": quarantine_validator(),
    }
    existing = set(database.list_collection_names())
    for name, validator in definitions.items():
        if name not in existing:
            database.create_collection(
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )
        else:
            database.command(
                "collMod",
                name,
                validator=validator,
                validationLevel="strict",
                validationAction="error",
            )
    database["invoices"].create_index([("ns", 1), ("cust_id", 1)])
    database["invoices"].create_index(
        [("ns", 1), ("invoice_no", 1)], unique=True
    )
    database["invoices_quarantine"].create_index([("ns", 1), ("anomaly_id", 1)])

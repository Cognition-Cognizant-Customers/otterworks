#!/usr/bin/env python3
"""Unit tests for the ``mongo_invoices`` document model.

Run with:
    uv run --no-project --with pymongo==4.10.1 --with pytest==8.3.3 \
      python3 -m pytest scripts/tp_mongo/test_invoices_model.py
"""
from __future__ import annotations

import datetime as _dt
import decimal
import sys
from pathlib import Path

import pytest
from bson import Decimal128

sys.path.insert(0, str(Path(__file__).resolve().parent))
from invoices_model import (
    DecodingError,
    NullRequiredField,
    UnparseableRequiredField,
    decimal128,
    decimal_value,
    parse_legacy_date,
)
from migrate_invoices import (
    invoice_document,
    normalize_line,
    quarantine_document,
)


def line(**overrides):
    base = {
        "line_id": "100",
        "invoice_no": "INV-1",
        "invoice_id": "1",
        "cust_id": "CUST-1",
        "cust_no": "CUST-1",
        "cust_name": "A Customer",
        "tenant_id": "demo",
        "line_no": 1,
        "line_type_cd": 2,
        "item_desc": "Item",
        "qty": decimal.Decimal(2),
        "unit_price": decimal.Decimal("1.25"),
        "amount": decimal.Decimal("2.50"),
        "tax_amt": decimal.Decimal("0.25"),
        "invoice_dt": "05-JAN-24",
        "service_period": "2024-01",
        "posted_yn": "Y",
        "gl_acct_csv": " 1000,2000 ",
        "batch_no": 85559852,
        "src_system": "ORACLE",
    }
    base.update(overrides)
    return base


def raw_quarantine_line(**overrides):
    return line(**overrides)


def test_legacy_date_retains_raw_value_when_unparseable():
    parsed, raw, invalid = parse_legacy_date("31-FEB-24")
    assert parsed is None
    assert raw == "31-FEB-24"
    assert invalid is True
    parsed, raw, invalid = parse_legacy_date("05-JAN-24")
    assert parsed == _dt.datetime(2024, 1, 5, tzinfo=_dt.timezone.utc)
    assert raw == "05-JAN-24"
    assert invalid is False
    parsed, raw, invalid = parse_legacy_date(None)
    assert parsed is None
    assert raw is None
    assert invalid is None


def test_money_rejects_float_and_produces_decimal128():
    with pytest.raises(TypeError):
        decimal_value(1.25)
    value = decimal128(decimal.Decimal("12.3400"))
    assert isinstance(value, Decimal128)
    assert value.to_decimal() == decimal.Decimal("12.3400")


def test_quarantine_attributes_missing_parent():
    document = quarantine_document(
        raw_quarantine_line(),
        "demo",
        85559852,
        "orphaned_rows",
        "parent INVOICE_HEADER is missing",
    )
    assert document["anomaly_id"] == "orphaned_rows"
    assert document["reason"] == "parent INVOICE_HEADER is missing"
    assert document["_id"] == "100"


@pytest.mark.parametrize(
    ("amount", "anomaly_id"),
    [
        (None, "null_amount"),
        ("not-money", "unparseable_amount"),
    ],
)
def test_quarantine_attributes_amount_anomalies(amount, anomaly_id):
    raw = raw_quarantine_line(amount=amount)
    if amount is None:
        with pytest.raises(NullRequiredField) as error:
            normalize_line(raw)
        assert error.value.field == "AMOUNT"
    else:
        with pytest.raises(decimal.InvalidOperation):
            normalize_line(raw)
    document = quarantine_document(
        raw,
        "demo",
        85559852,
        anomaly_id,
        f"AMOUNT is {anomaly_id.removeprefix('unparseable_')}",
    )
    assert document["anomaly_id"] == anomaly_id
    assert document["amount"] is None


def test_quarantine_attributes_null_invoice_id():
    document = quarantine_document(
        raw_quarantine_line(invoice_id=None),
        "demo",
        85559852,
        "null_invoice_id",
        "INVOICE_ID is NULL",
    )
    assert document["invoice_id"] is None
    assert document["anomaly_id"] == "null_invoice_id"


def test_quarantine_preserves_non_utf8_bytes_as_hex():
    raw = raw_quarantine_line(cust_name=b"\xff")
    with pytest.raises(DecodingError):
        normalize_line(raw)
    document = quarantine_document(
        raw,
        "demo",
        85559852,
        "invalid_encoding",
        "a source string could not be decoded as UTF-8",
    )
    assert document["source_row"]["CUST_NAME"] == "0xff"


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("line_no", None, NullRequiredField),
        ("line_type_cd", "not-an-int", UnparseableRequiredField),
        ("qty", None, NullRequiredField),
        ("unit_price", "not-money", UnparseableRequiredField),
        ("tax_amt", None, NullRequiredField),
    ],
)
def test_required_numeric_fields_are_explicitly_attributed(field, value, error_type):
    with pytest.raises(error_type) as error:
        normalize_line(raw_quarantine_line(**{field: value}))
    assert error.value.field == field.upper()


def test_invoice_total_is_exact_line_sum_and_flags_legacy_mismatch():
    normalized_lines = [
        normalize_line(
            raw_quarantine_line(
                line_id="100",
                line_no=2,
                amount=decimal.Decimal("1.10"),
                tax_amt=decimal.Decimal("0.10"),
            )
        ),
        normalize_line(
            raw_quarantine_line(
                line_id="101",
                line_no=1,
                amount=decimal.Decimal("2.20"),
                tax_amt=decimal.Decimal("0.20"),
            )
        ),
    ]
    document = invoice_document(
        {
            "invoice_id": "1",
            "invoice_no": "INV-1",
            "cust_id": "CUST-1",
            "tenant_id": "demo",
            "status_cd": 1,
            "invoice_dt": "05-JAN-24",
            "due_dt": "31-FEB-24",
            "total_amt": decimal.Decimal("99.00"),
        },
        normalized_lines,
        "demo",
        85559852,
    )
    assert document["total_amt"].to_decimal() == decimal.Decimal("3.30")
    assert document["tax_amt"].to_decimal() == decimal.Decimal("0.30")
    assert document["legacy_total_matches_lines"] is False
    assert "legacy_total_mismatch" in document["data_quality"]
    assert document["lines"][0]["line_id"] == "101"
    assert document["due_dt"] is None
    assert document["due_dt_raw"] == "31-FEB-24"


def test_null_invoice_dates_are_attributed():
    document = invoice_document(
        {
            "invoice_id": "1",
            "invoice_no": "INV-1",
            "cust_id": "CUST-1",
            "tenant_id": "demo",
            "status_cd": 1,
            "invoice_dt": None,
            "due_dt": None,
            "total_amt": decimal.Decimal(0),
        },
        [],
        "demo",
        85559852,
    )
    assert document["invoice_dt"] is None
    assert document["due_dt"] is None
    assert "null_invoice_dt" in document["data_quality"]
    assert "null_due_dt" in document["data_quality"]


def test_duplicate_invoice_no_is_attributed():
    document = invoice_document(
        {
            "invoice_id": "1",
            "invoice_no": "INV-1",
            "cust_id": "CUST-1",
            "tenant_id": "demo",
            "status_cd": 1,
            "invoice_dt": "05-JAN-24",
            "due_dt": "06-JAN-24",
            "total_amt": decimal.Decimal(0),
            "duplicate_invoice_no": True,
        },
        [],
        "demo",
        85559852,
    )
    assert "duplicate_invoice_no" in document["data_quality"]

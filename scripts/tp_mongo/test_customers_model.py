#!/usr/bin/env python3
"""Unit tests for the ``mongo_customers`` document model.

Run with:
    uv run --no-project --with pymongo==4.10.1 --with pytest==8.3.3 \\
      python3 -m pytest scripts/tp_mongo/test_customers_model.py
"""
from __future__ import annotations

import datetime as _dt
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from customers_model import (  # noqa: E402
    ANOMALY_DIRTY_SIGNUP_DT,
    ANOMALY_MALFORMED_RELATED_ACCT_IDS,
    AnomalyError,
    build_customer,
    parse_csv_list,
    parse_legacy_date,
    type_attr_value,
)

# Every dirty value the seed plants in SIGNUP_DT, verbatim.
DIRTY_DATES = ["31-FEB-24", "00-XXX-00", "99-999-99", "1/1/1900", "N/A",
               "29-FEB-23", "  -   -  ", "12-13-201"]
# Every malformed value the seed plants in RELATED_ACCT_IDS, verbatim.
MALFORMED_LISTS = [",,", "12345,,67890,", "A;B;C", " , 99 ,", "NULL,NONE,",
                   "0000000000000000000000,"]


def row(**overrides):
    base = {
        "CUST_ID": "0" * 32, "CUST_NO": "DEMO-000000001", "CUST_NAME": "A Customer",
        "TENANT_ID": "t-1", "CUR_BAL_AMT": Decimal("12.34"),
        "SIGNUP_DT": "05-JAN-20", "RELATED_ACCT_IDS": "10,20",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("raw", DIRTY_DATES)
def test_dirty_dates_never_become_dates(raw):
    with pytest.raises(AnomalyError) as err:
        parse_legacy_date(raw, "SIGNUP_DT", ANOMALY_DIRTY_SIGNUP_DT)
    assert err.value.anomaly_id == ANOMALY_DIRTY_SIGNUP_DT
    doc, anomalies = build_customer(row(SIGNUP_DT=raw), [], "demo")
    assert "signup_date" not in doc  # absent, never a plausible substitute
    assert [a.anomaly_id for a in anomalies] == [ANOMALY_DIRTY_SIGNUP_DT]


def test_valid_date_becomes_bson_date():
    assert parse_legacy_date("05-JAN-20", "SIGNUP_DT", "x") == _dt.datetime(2020, 1, 5)
    assert parse_legacy_date("05-JAN-99", "SIGNUP_DT", "x") == _dt.datetime(1999, 1, 5)


@pytest.mark.parametrize("raw", MALFORMED_LISTS)
def test_malformed_lists_are_never_truncated(raw):
    with pytest.raises(AnomalyError):
        parse_csv_list(raw, "RELATED_ACCT_IDS", ANOMALY_MALFORMED_RELATED_ACCT_IDS)
    doc, anomalies = build_customer(row(RELATED_ACCT_IDS=raw), [], "demo")
    assert "related_acct_ids" not in doc
    assert [a.anomaly_id for a in anomalies] == [ANOMALY_MALFORMED_RELATED_ACCT_IDS]


def test_well_formed_list_becomes_array():
    assert parse_csv_list("10,20,30", "RELATED_ACCT_IDS", "x") == ["10", "20", "30"]


def test_sparse_columns_are_absent_not_null():
    doc, anomalies = build_customer(row(DBA_NAME=None, TERMINATE_DT=None,
                                        UDF_01=None), [], "demo")
    assert anomalies == []
    assert "dba_name" not in doc and "terminate_date" not in doc
    assert "udf" not in doc
    assert None not in doc.values()


def test_multiple_anomalies_on_one_row_are_all_named():
    doc, anomalies = build_customer(
        row(SIGNUP_DT="31-FEB-24", RELATED_ACCT_IDS=",,"), [], "demo")
    assert sorted(a.anomaly_id for a in anomalies) == [
        ANOMALY_DIRTY_SIGNUP_DT, ANOMALY_MALFORMED_RELATED_ACCT_IDS]
    assert doc["quarantine_anomaly_ids"] == sorted(
        {ANOMALY_DIRTY_SIGNUP_DT, ANOMALY_MALFORMED_RELATED_ACCT_IDS})


def test_eav_rows_fold_without_dropping_any():
    attrs = [
        {"EAV_ID": 1, "ATTR_NAME": "LEGACY_TIER", "ATTR_VALUE": "GOLD",
         "ATTR_TYPE": "STR"},
        {"EAV_ID": 2, "ATTR_NAME": "LEGACY_TIER", "ATTR_VALUE": "SILVER",
         "ATTR_TYPE": "STR"},
        {"EAV_ID": 3, "ATTR_NAME": "Y2K_VERIFIED", "ATTR_VALUE": "Y",
         "ATTR_TYPE": "STR"},
    ]
    doc, _ = build_customer(row(), attrs, "demo")
    assert doc["attributes"] == {"legacy_tier": "SILVER", "y2k_verified": True}
    assert len(doc["attributes_entries"]) == len(attrs)
    assert [e["current"] for e in doc["attributes_entries"]] == [False, True, True]


def test_attr_values_are_typed():
    assert type_attr_value("Y") is True
    assert type_attr_value("FALSE") is False
    assert type_attr_value("42") == 42
    assert type_attr_value("4.5") == 4.5
    assert type_attr_value("see ticket 12") == "see ticket 12"

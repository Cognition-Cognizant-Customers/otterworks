"""Unit tests for the finance report Lambda (fixture run_mode).

Golden-parity tests regenerate the legacy inputs deterministically:

  export OTTERWORKS_LEGACY_ROOT=/tmp/ow-legacy-report
  make legacy-etl-gen-data NS=DEMO
  python3 scripts/tp_aws/gen_anomaly_file.py DEMO
  TP_FAKETIME='2026-01-15 00:00:00' scripts/tp-run-deterministic.sh \
    bash -c 'etl/legacy-extra/jobs/sftp_ingest_poll.ksh; \
             etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh; \
             etl/legacy-extra/jobs/finance_excel_report.pl'

then compare aggregate() output against the golden report bytes
(md5 300862b738fdb8b6add8d1007362c0e0).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from handler import HEADER, aggregate, perl_num, record_type_name

GOLDEN_ROOT = Path(os.environ.get("OTTERWORKS_LEGACY_ROOT", "/tmp/ow-legacy-report"))
GOLDEN_REPORT_MD5 = "300862b738fdb8b6add8d1007362c0e0"


def golden_available() -> bool:
    return (GOLDEN_ROOT / "reports" / "finance_billing_20260115.csv").exists()


@pytest.mark.skipif(not golden_available(), reason="golden legacy run not present")
def test_byte_identical_to_golden_report():
    psv_files = sorted((GOLDEN_ROOT / "parsed").glob("CUSTBILL*.psv"))
    assert psv_files, "expected parsed golden inputs"
    report = aggregate([p.read_bytes() for p in psv_files])
    golden = (GOLDEN_ROOT / "reports" / "finance_billing_20260115.csv").read_bytes()
    assert report == golden
    assert hashlib.md5(report).hexdigest() == GOLDEN_REPORT_MD5


@pytest.mark.skipif(not golden_available(), reason="golden legacy run not present")
def test_xls_golden_is_byte_identical_copy_of_csv():
    csv = (GOLDEN_ROOT / "reports" / "finance_billing_20260115.csv").read_bytes()
    xls = (GOLDEN_ROOT / "reports" / "finance_billing_20260115.xls").read_bytes()
    assert csv == xls


def test_empty_input_writes_header_only():
    assert aggregate([]) == HEADER


def test_empty_first_field_skipped():
    body = b"|NO CUST|2025-01-01|10.00|USD|01\nC1|X|2025-01-01|10.00|USD|01\n"
    assert aggregate([body]) == HEADER + b"USD,INVOICE,1,10.00\n"


def test_short_record_attributes_to_unknown_bucket_sorted_last():
    body = (
        b"C000000903|SHORTY GMBH|--|0.00||\n"
        b"C1|X|2025-01-01|10.00|USD|01\n"
    )
    assert aggregate([body]) == (
        HEADER + b"USD,INVOICE,1,10.00\n" + b",UNKNOWN(),1,0.00\n"
    )


def test_extra_fields_dropped_and_nonutf8_name_tolerated():
    body = b"C1|BAD \xa3 NAME|2025-01-01|5.00|GBP|02|extra|fields\n"
    assert aggregate([body]) == HEADER + b"GBP,CREDIT,1,5.00\n"


def test_missing_amount_coerces_to_zero():
    body = b"C1|X|2025-01-01||USD|01\nC2|Y|2025-01-01|2.50|USD|01\n"
    assert aggregate([body]) == HEADER + b"USD,INVOICE,2,2.50\n"


def test_unknown_record_type_named():
    body = b"C1|X|2025-01-01|1.00|USD|99\n"
    assert aggregate([body]) == HEADER + b"USD,UNKNOWN(99),1,1.00\n"


def test_perl_numeric_coercion():
    assert perl_num(b"") == 0.0
    assert perl_num(b"abc") == 0.0
    assert perl_num(b"12.5xyz") == 12.5
    assert perl_num(b"-3.25") == -3.25


def test_record_type_names():
    assert record_type_name(b"01") == b"INVOICE"
    assert record_type_name(b"02") == b"CREDIT"
    assert record_type_name(b"") == b"UNKNOWN()"


def test_idempotent_aggregation():
    psv = b"C1|X|2025-01-01|10.00|USD|01\n"
    assert aggregate([psv]) == aggregate([psv])

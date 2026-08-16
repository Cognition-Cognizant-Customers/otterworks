"""S3-level tests for the report Lambda handler using moto (fixture run_mode)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

import handler

GOLDEN_ROOT = Path(os.environ.get("OTTERWORKS_LEGACY_ROOT", "/tmp/ow-legacy-report"))
GOLDEN_REPORT_MD5 = "300862b738fdb8b6add8d1007362c0e0"
BUCKET = "ow-tp-demo-pipeline-000000000000"


@pytest.fixture
def s3_bucket(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("PIPELINE_BUCKET", BUCKET)
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket=BUCKET)
        yield s3


def invoke(report_date="20260115", ns="demo"):
    return handler.handler({"ns": ns, "report_date": report_date}, None)


def test_empty_parsed_prefix_writes_header_only(s3_bucket):
    result = invoke()
    assert result["input_files"] == 0
    body = s3_bucket.get_object(Bucket=BUCKET, Key=result["csv_key"])["Body"].read()
    assert body == handler.HEADER


def golden_available() -> bool:
    return (GOLDEN_ROOT / "reports" / "finance_billing_20260115.csv").exists()


@pytest.mark.skipif(not golden_available(), reason="golden legacy run not present")
def test_golden_parity_and_idempotent_rerun(s3_bucket):
    for p in sorted((GOLDEN_ROOT / "parsed").glob("CUSTBILL*.psv")):
        s3_bucket.put_object(Bucket=BUCKET, Key=f"parsed/{p.name}", Body=p.read_bytes())
    # a non-CUSTBILL object under parsed/ must be ignored
    s3_bucket.put_object(Bucket=BUCKET, Key="parsed/OTHER_FILE.psv", Body=b"x|y\n")

    first = invoke()
    csv1 = s3_bucket.get_object(Bucket=BUCKET, Key=first["csv_key"])["Body"].read()
    xls1 = s3_bucket.get_object(Bucket=BUCKET, Key=first["xls_key"])["Body"].read()
    assert hashlib.md5(csv1).hexdigest() == GOLDEN_REPORT_MD5
    assert xls1 == csv1

    second = invoke()  # idempotent rerun: identical bytes, same keys
    csv2 = s3_bucket.get_object(Bucket=BUCKET, Key=second["csv_key"])["Body"].read()
    assert csv2 == csv1
    reports = s3_bucket.list_objects_v2(Bucket=BUCKET, Prefix="reports/")
    assert {o["Key"] for o in reports["Contents"]} == {first["csv_key"], first["xls_key"]}


def test_report_date_comes_from_event_not_clock(s3_bucket):
    result = invoke(report_date="19990101")
    assert result["csv_key"] == "reports/finance_billing_19990101.csv"


def test_invalid_report_date_raises(s3_bucket):
    with pytest.raises(ValueError):
        invoke(report_date="2026-01-15")

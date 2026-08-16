"""Finance billing report Lambda (replaces etl/legacy-extra/jobs/finance_excel_report.pl).

Aggregates every parsed/CUSTBILL*.psv object in the pipeline bucket into
reports/finance_billing_<report_date>.csv, byte-identical to the legacy
report, plus a byte-identical .xls copy (the 2004 "conversion", preserved
for parity). The report date comes from the invocation event, never from
the wall clock, and no timestamps are embedded in the artifact bytes.

Byte transparency: .psv lines are split on the pipe byte without a UTF-8
decode layer (name fields may carry non-UTF-8 bytes); only the customer,
amount, currency, and record-type fields are interpreted. Perl split
semantics are matched: fields beyond the sixth are dropped, missing or
empty amount coerces to 0, and empty currency/record-type attribute to
the ",UNKNOWN()" bucket. Sendmail delivery is not replicated (the legacy
path is a silent no-op).
"""
from __future__ import annotations

import os
import re

import boto3

HEADER = b"Currency,RecordType,RecordCount,TotalAmount\n"
PSV_NAME = re.compile(r"^CUSTBILL.*\.psv$")
NUMERIC_PREFIX = re.compile(rb"^[ \t]*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def perl_num(field: bytes) -> float:
    """Perl numeric coercion: leading numeric prefix, else 0."""
    m = NUMERIC_PREFIX.match(field)
    return float(m.group(0)) if m else 0.0


def record_type_name(rt: bytes) -> bytes:
    if rt == b"01":
        return b"INVOICE"
    if rt == b"02":
        return b"CREDIT"
    return b"UNKNOWN(" + rt + b")"


def aggregate(psv_bodies: list[bytes]) -> bytes:
    """Aggregate raw .psv bytes into the legacy report CSV bytes."""
    totals: dict[bytes, float] = {}
    counts: dict[bytes, int] = {}
    for body in psv_bodies:
        for line in body.split(b"\n"):
            line = line.rstrip(b"\r")
            if line == b"":
                continue
            fields = line.split(b"|")
            cust = fields[0]
            if cust == b"":
                continue
            amt = fields[3] if len(fields) > 3 else b""
            ccy = fields[4] if len(fields) > 4 else b""
            rt = fields[5] if len(fields) > 5 else b""
            key = ccy + b"|" + rt
            totals[key] = totals.get(key, 0.0) + perl_num(amt)
            counts[key] = counts.get(key, 0) + 1

    out = [HEADER]
    for key in sorted(totals):  # bytewise == LC_ALL=C sort
        ccy, _, rt = key.partition(b"|")
        out.append(
            ccy + b"," + record_type_name(rt)
            + f",{counts[key]:d},{totals[key]:.2f}\n".encode("ascii")
        )
    return b"".join(out)


def handler(event, context):
    ns = event.get("ns") or os.environ["NS"]
    report_date = event["report_date"]
    if not re.fullmatch(r"[0-9]{8}", report_date):
        raise ValueError(f"report_date must be YYYYMMDD, got {report_date!r}")
    bucket = os.environ.get("PIPELINE_BUCKET") or (
        f"ow-tp-{ns}-pipeline-{os.environ['ACCOUNT_ID']}"
    )

    s3 = boto3.client("s3")
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="parsed/"):
        for obj in page.get("Contents", []):
            name = obj["Key"].rsplit("/", 1)[-1]
            if PSV_NAME.match(name):
                keys.append(obj["Key"])
    keys.sort()  # legacy processes files in sorted order

    bodies = [s3.get_object(Bucket=bucket, Key=k)["Body"].read() for k in keys]
    report = aggregate(bodies)

    csv_key = f"reports/finance_billing_{report_date}.csv"
    xls_key = f"reports/finance_billing_{report_date}.xls"
    for key in (csv_key, xls_key):
        s3.put_object(Bucket=bucket, Key=key, Body=report, ContentType="text/csv")

    return {
        "ns": ns,
        "report_date": report_date,
        "bucket": bucket,
        "input_files": len(keys),
        "report_rows": report.count(b"\n") - 1,
        "csv_key": csv_key,
        "xls_key": xls_key,
    }

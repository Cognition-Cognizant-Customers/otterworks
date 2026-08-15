"""Generate the finance report from parsed CUSTBILL PSV objects."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from functools import lru_cache
import os
from zoneinfo import ZoneInfo

import boto3

from pipeline import PARSED_PREFIX, env, report_key


@lru_cache(maxsize=1)
def _s3():
    return boto3.client("s3")


def aggregate(psv_lines: Iterable[str]) -> list[dict]:
    """Aggregate parsed records by currency and record type."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}

    for line in psv_lines:
        if not line.strip():
            continue
        fields = line.split("|")
        if len(fields) != 6:
            raise ValueError(f"expected 6 PSV fields, got {len(fields)}")
        cust, _name, _date, amount, currency, record_type = fields
        if not cust:
            continue
        try:
            value = float(amount)
        except ValueError as exc:
            raise ValueError(f"invalid amount {amount!r}") from exc

        key = f"{currency}|{record_type}"
        totals[key] = totals.get(key, 0.0) + value
        counts[key] = counts.get(key, 0) + 1

    rows = []
    for key in sorted(totals):
        currency, record_type = key.split("|", 1)
        label = {
            "01": "INVOICE",
            "02": "CREDIT",
        }.get(record_type, f"UNKNOWN({record_type})")
        rows.append(
            {
                "currency": currency,
                "record_type": label,
                "count": counts[key],
                "total": totals[key],
            }
        )
    return rows


def render_csv(rows: Iterable[dict]) -> str:
    """Render report rows using the legacy CSV byte format."""
    lines = ["Currency,RecordType,RecordCount,TotalAmount"]
    lines.extend(
        f"{row['currency']},{row['record_type']},{row['count']},{row['total']:.2f}"
        for row in rows
    )
    return "\n".join(lines) + "\n"


def report_stamp() -> str:
    """Return today's report stamp in the configured timezone."""
    timezone = ZoneInfo(os.environ.get("TZ", "UTC"))
    return datetime.now(timezone).strftime("%Y%m%d")


def handler(event, context):
    """Aggregate every parsed PSV for the event namespace and publish reports."""
    ns = event.get("ns") if event else None
    if not ns:
        raise ValueError("event must include a non-empty ns")

    bucket = env("BUCKET")
    prefix = f"{PARSED_PREFIX}/{ns}/"
    client = _s3()
    keys = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(
            item["Key"]
            for item in page.get("Contents", [])
            if item["Key"].endswith(".psv")
        )

    lines = []
    for key in sorted(keys):
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        lines.extend(body.decode("utf-8").splitlines())

    rows = aggregate(lines)
    report_bytes = render_csv(rows).encode("utf-8")
    stamp = report_stamp()
    csv_key = report_key(ns, f"finance_billing_{stamp}.csv")
    xls_key = report_key(ns, f"finance_billing_{stamp}.xls")
    client.put_object(Bucket=bucket, Key=csv_key, Body=report_bytes)
    client.put_object(Bucket=bucket, Key=xls_key, Body=report_bytes)

    return {
        "ns": ns,
        "report_key": csv_key,
        "xls_key": xls_key,
        "rows": len(rows),
        "files_aggregated": len(keys),
    }

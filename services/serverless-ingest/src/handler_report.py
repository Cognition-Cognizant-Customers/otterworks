"""Step Functions task: regenerate the finance billing report for a namespace.

Reads every parsed/<ns>/CUSTBILL*.psv object (sorted by key, matching the
legacy report's sorted readdir), aggregates totals by currency + record type,
and writes reports/<ns>/finance_billing_<YYYYMMDD>.csv plus the traditional
byte-identical .xls copy.
"""

import os
import time

import boto3

from custbill import finance_report

s3 = boto3.client("s3")

BUCKET = os.environ.get("BUCKET", "")


def handler(event, context):
    bucket = event.get("bucket") or BUCKET
    ns = event["ns"]

    prefix = f"parsed/{ns}/"
    keys = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            name = obj["Key"].split("/")[-1]
            if name.startswith("CUSTBILL") and name.endswith(".psv"):
                keys.append(obj["Key"])

    lines: list[str] = []
    for key in sorted(keys):
        text = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        lines.extend(line for line in text.splitlines() if line)

    csv = finance_report(lines)
    stamp = time.strftime("%Y%m%d", time.gmtime())
    csv_key = f"reports/{ns}/finance_billing_{stamp}.csv"
    xls_key = f"reports/{ns}/finance_billing_{stamp}.xls"
    s3.put_object(Bucket=bucket, Key=csv_key, Body=csv.encode("utf-8"))
    s3.put_object(Bucket=bucket, Key=xls_key, Body=csv.encode("utf-8"))

    return {"bucket": bucket, "ns": ns, "report_key": csv_key, "files": len(keys)}

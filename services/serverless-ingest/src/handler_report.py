"""Step Functions task: regenerate the finance billing report for a namespace.

Reads every parsed/<ns>/CUSTBILL*.psv object (sorted by key, matching the
legacy report's sorted readdir), aggregates totals by currency + record type,
and writes reports/<ns>/finance_billing_<YYYYMMDD>.csv plus the traditional
byte-identical .xls copy.
"""

import os
import time

import boto3
from botocore.exceptions import ClientError

from custbill import finance_report

s3 = boto3.client("s3")
dynamodb = boto3.client("dynamodb")

BUCKET = os.environ.get("BUCKET", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "")

LOCK_REC = "_report_lock"
LOCK_TTL_SECONDS = 120
LOCK_WAIT_SECONDS = 90


def _acquire_lock(ns: str) -> None:
    """Per-namespace mutex so concurrent executions serialize the report.

    List + write must be atomic relative to sibling executions: without the
    lock, an execution that listed parsed/<ns>/ early could overwrite a more
    complete report written by a sibling.
    """
    deadline = time.time() + LOCK_WAIT_SECONDS
    while True:
        now = int(time.time())
        try:
            dynamodb.put_item(
                TableName=TABLE_NAME,
                Item={
                    "ns": {"S": ns},
                    "rec": {"S": LOCK_REC},
                    "lock_expires": {"N": str(now + LOCK_TTL_SECONDS)},
                },
                ConditionExpression="attribute_not_exists(ns) OR lock_expires < :now",
                ExpressionAttributeValues={":now": {"N": str(now)}},
            )
            return
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            if time.time() >= deadline:
                raise TimeoutError(f"could not acquire report lock for ns={ns}")
            time.sleep(3)


def _release_lock(ns: str) -> None:
    dynamodb.delete_item(
        TableName=TABLE_NAME,
        Key={"ns": {"S": ns}, "rec": {"S": LOCK_REC}},
    )


def handler(event, context):
    bucket = event.get("bucket") or BUCKET
    ns = event["ns"]

    _acquire_lock(ns)
    try:
        return _build_report(bucket, ns)
    finally:
        _release_lock(ns)


def _build_report(bucket: str, ns: str):
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

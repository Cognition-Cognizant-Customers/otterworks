"""Step Functions task: regenerate the finance billing report for a namespace.

Reads every parsed/<ns>/CUSTBILL*.psv object (sorted by key, matching the
legacy report's sorted readdir), aggregates totals by currency + record type,
and writes reports/<ns>/finance_billing_<YYYYMMDD>.csv plus the traditional
byte-identical .xls copy.
"""

import os
import time
import uuid

import boto3
from botocore.exceptions import ClientError

from custbill import finance_report

s3 = boto3.client("s3")
dynamodb = boto3.client("dynamodb")

BUCKET = os.environ.get("BUCKET", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "")

LOCK_REC = "_report_lock"
# Lease outlives the Lambda timeout (120s) so a live holder never loses the
# lock mid-report; expiry only reclaims locks from crashed executions
LOCK_TTL_SECONDS = 180
LOCK_WAIT_SECONDS = 90


def _acquire_lock(ns: str, owner: str) -> None:
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
                    "lock_owner": {"S": owner},
                },
                ConditionExpression="attribute_not_exists(#ns) OR lock_expires < :now",
                ExpressionAttributeNames={"#ns": "ns"},
                ExpressionAttributeValues={":now": {"N": str(now)}},
            )
            return
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            if time.time() >= deadline:
                raise TimeoutError(f"could not acquire report lock for ns={ns}")
            time.sleep(3)


def _release_lock(ns: str, owner: str) -> None:
    try:
        dynamodb.delete_item(
            TableName=TABLE_NAME,
            Key={"ns": {"S": ns}, "rec": {"S": LOCK_REC}},
            ConditionExpression="lock_owner = :me",
            ExpressionAttributeValues={":me": {"S": owner}},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise


def handler(event, context):
    bucket = event.get("bucket") or BUCKET
    ns = event["ns"]

    owner = getattr(context, "aws_request_id", None) or str(uuid.uuid4())
    _acquire_lock(ns, owner)
    try:
        return _build_report(bucket, ns)
    finally:
        _release_lock(ns, owner)


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
        text = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("latin-1")
        lines.extend(line for line in text.split("\n") if line)

    csv = finance_report(lines)
    # Legacy finance_excel_report.pl stamps with localtime; honor TZ if set
    stamp = time.strftime("%Y%m%d", time.localtime())
    csv_key = f"reports/{ns}/finance_billing_{stamp}.csv"
    xls_key = f"reports/{ns}/finance_billing_{stamp}.xls"
    s3.put_object(Bucket=bucket, Key=csv_key, Body=csv.encode("latin-1"))
    s3.put_object(Bucket=bucket, Key=xls_key, Body=csv.encode("latin-1"))

    return {"bucket": bucket, "ns": ns, "report_key": csv_key, "files": len(keys)}

"""Parser Lambda: consumes S3 Object Created events from the shared SQS queue.

Each SQS record carries an EventBridge envelope for an incoming/ object.
The handler fetches the object bytes, runs the byte-exact legacy parse,
writes parsed/<basename>.psv, and records per-file counts plus any detected
anomalies in the batch-state DynamoDB table.

Idempotent on SQS redelivery: object writes and ledger keys are fully
deterministic, so a reprocess overwrites identical state. A message whose
object cannot be fetched raises, so SQS redrives it to the DLQ after
maxReceiveCount receives — no silent suppression.
"""
from __future__ import annotations

import json
import os
import posixpath

import boto3

from parser_core import parse_custbill

INCOMING_PREFIX = "incoming/"
PARSED_PREFIX = "parsed/"

_s3 = None
_ddb_table = None


def _clients():
    global _s3, _ddb_table
    if _s3 is None:
        _s3 = boto3.client("s3")
    if _ddb_table is None:
        _ddb_table = boto3.resource("dynamodb").Table(os.environ["BATCH_STATE_TABLE"])
    return _s3, _ddb_table


def process_object(bucket: str, key: str, s3, table, ns: str) -> dict | None:
    name = posixpath.basename(key)
    if not key.startswith(INCOMING_PREFIX) or not (
        name.startswith("CUSTBILL") and name.endswith(".dat")
    ):
        print(f"skipping non-CUSTBILL object s3://{bucket}/{key}")
        return None

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    result = parse_custbill(body)

    basename = name[: -len(".dat")]
    out_key = f"{PARSED_PREFIX}{basename}.psv"
    s3.put_object(Bucket=bucket, Key=out_key, Body=result.psv)

    table.put_item(
        Item={
            "pk": f"file#{ns}",
            "sk": f"{basename}.psv",
            "record_count": result.record_count,
            "trailer_count": result.trailer_count,
        }
    )
    for anomaly_id in result.anomalies:
        table.put_item(
            Item={
                "pk": f"anomaly#{ns}",
                "sk": f"{basename}#{anomaly_id}",
                "anomaly_id": anomaly_id,
            }
        )

    print(
        f"parsed {basename}: {result.record_count} records "
        f"(trailer says {result.trailer_count if result.trailer_count is not None else '?'}), "
        f"anomalies={result.anomalies}"
    )
    return {
        "basename": basename,
        "out_key": out_key,
        "record_count": result.record_count,
        "trailer_count": result.trailer_count,
        "anomalies": result.anomalies,
    }


def lambda_handler(event, context, s3=None, table=None):
    if s3 is None or table is None:
        s3, table = _clients()
    ns = os.environ["NS"]

    for sqs_record in event.get("Records", []):
        envelope = json.loads(sqs_record["body"])
        detail = envelope["detail"]
        bucket = detail["bucket"]["name"]
        key = detail["object"]["key"]
        # Any failure here (missing object, denied read, bad envelope) raises:
        # SQS redelivers and the shared redrive policy lands the message on
        # the DLQ after maxReceiveCount receives.
        process_object(bucket, key, s3, table, ns)

    return {"processed": len(event.get("Records", []))}

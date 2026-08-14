"""SQS-triggered Lambda: starts a Step Functions execution per landed CUSTBILL file.

Event path: S3 (landing/<ns>/CUSTBILL_*.dat) -> EventBridge rule -> SQS -> here.
The namespace is derived from the landing key prefix (landing/<ns>/...), falling
back to the CUSTBILL_<NS>_NNN.dat filename.
"""

import json
import os
import re
import time

import boto3

sfn = boto3.client("stepfunctions")

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]

_FNAME_NS = re.compile(r"^CUSTBILL_([A-Za-z0-9]+)_\d+\.dat$")
_FNAME_OK = re.compile(r"^CUSTBILL.*\.dat$")


def _ns_from_key(key: str) -> str | None:
    parts = key.split("/")
    if not _FNAME_OK.match(parts[-1]):
        return None
    if len(parts) >= 3 and parts[0] == "landing":
        return parts[1].lower()
    m = _FNAME_NS.match(parts[-1])
    return m.group(1).lower() if m else None


def _process_record(record) -> None:
    body = json.loads(record["body"])
    detail = body.get("detail", {})
    bucket = detail.get("bucket", {}).get("name")
    key = detail.get("object", {}).get("key")
    if not bucket or not key:
        return
    ns = _ns_from_key(key)
    if ns is None:
        print(f"skipping non-CUSTBILL object: s3://{bucket}/{key}")
        return
    name = re.sub(r"[^A-Za-z0-9_-]", "-", key)[-60:] + f"-{int(time.time() * 1000)}"
    sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=name,
        input=json.dumps({"bucket": bucket, "key": key, "ns": ns}),
    )
    print(f"started pipeline for s3://{bucket}/{key} ns={ns}")


def handler(event, context):
    # Partial-batch response: only failing messages are retried / DLQ'd
    failures = []
    for record in event.get("Records", []):
        try:
            _process_record(record)
        except Exception as e:  # noqa: BLE001 - isolate per-message failures
            print(f"failed to process message {record.get('messageId')}: {e}")
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}

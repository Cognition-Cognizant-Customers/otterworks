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


def _ns_from_key(key: str) -> str | None:
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] == "landing":
        return parts[1].lower()
    m = _FNAME_NS.match(parts[-1])
    return m.group(1).lower() if m else None


def handler(event, context):
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        detail = body.get("detail", {})
        bucket = detail.get("bucket", {}).get("name")
        key = detail.get("object", {}).get("key")
        if not bucket or not key:
            continue
        ns = _ns_from_key(key)
        if ns is None:
            print(f"skipping non-CUSTBILL object: s3://{bucket}/{key}")
            continue
        name = re.sub(r"[^A-Za-z0-9_-]", "-", key)[-60:] + f"-{int(time.time() * 1000)}"
        sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=name,
            input=json.dumps({"bucket": bucket, "key": key, "ns": ns}),
        )
        print(f"started pipeline for s3://{bucket}/{key} ns={ns}")
    return {"ok": True}

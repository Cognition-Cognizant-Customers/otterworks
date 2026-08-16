"""ow-tp-trigger — SQS-driven starter for the CUSTBILL pipeline.

Replaces ``etl/legacy-extra/jobs/sftp_ingest_poll.ksh``: instead of polling the
drop directory, comparing byte counts to guess whether a file has settled and
leaving a lock file behind, the S3 ``Object Created`` event (EventBridge -> SQS)
is the arrival signal, and each landed file becomes exactly one Step Functions
execution.

Delivery semantics: SQS is at-least-once, so the execution name is derived from
the object key plus the event time — a redelivery of the same event reuses the
name and ``ExecutionAlreadyExists`` is treated as success. Records that raise are
returned in ``batchItemFailures`` so only they are retried and eventually land in
the DLQ, rather than replaying the whole batch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any

import boto3

import pipeline

LOGGER = logging.getLogger()
LOGGER.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

FILENAME_RE = re.compile(r"^CUSTBILL.*\.dat$")
UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")
MAX_EXECUTION_NAME = 80

_SFN = None


def _client():
    global _SFN
    if _SFN is None:
        _SFN = boto3.client("stepfunctions")
    return _SFN


def execution_name(ns: str, filename: str, key: str, event_time: str) -> str:
    """Deterministic per (file, event) execution name, <=80 safe characters."""
    digest = hashlib.md5(f"{key}{event_time}".encode()).hexdigest()[:8]
    stem = filename.rsplit(".", 1)[0]
    suffix = f"-{digest}"
    prefix = UNSAFE_NAME_CHARS.sub("-", f"{ns}-{stem}")
    return prefix[: MAX_EXECUTION_NAME - len(suffix)] + suffix


def _is_pipeline_key(key: str) -> bool:
    parts = key.split("/")
    return (
        len(parts) == 3
        and parts[0] == pipeline.LANDING_PREFIX
        and bool(FILENAME_RE.match(parts[2]))
    )


def _start(record: dict[str, Any]) -> None:
    envelope = json.loads(record["body"])
    detail = envelope["detail"]
    bucket = detail["bucket"]["name"]
    key = detail["object"]["key"]

    if not _is_pipeline_key(key):
        LOGGER.info("skipping non-CUSTBILL key %s", key)
        return

    ns = pipeline.namespace_from_key(key)
    filename = key.rsplit("/", 1)[-1]
    state_machine_arn = pipeline.env("STATE_MACHINE_ARN")
    name = execution_name(ns, filename, key, envelope.get("time", ""))
    payload = {"ns": ns, "bucket": bucket, "key": key, "filename": filename}

    client = _client()
    try:
        client.start_execution(
            stateMachineArn=state_machine_arn,
            name=name,
            input=json.dumps(payload),
        )
    except client.exceptions.ExecutionAlreadyExists:
        LOGGER.info("execution %s already started for %s", name, key)
        return
    LOGGER.info("started execution %s for %s", name, key)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    failures: list[dict[str, str]] = []

    for record in event.get("Records", []):
        try:
            _start(record)
        except Exception:
            LOGGER.exception("record %s failed", record.get("messageId"))
            failures.append({"itemIdentifier": record.get("messageId", "")})

    return {"batchItemFailures": failures}

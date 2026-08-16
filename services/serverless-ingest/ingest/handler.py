"""Event-driven ingest Lambda for the AWS serverless track.

Replaces etl/legacy-extra/jobs/sftp_ingest_poll.ksh. Invoked by EventBridge
per S3 "Object Created" event under landing/ in the pipeline bucket:

  * stages the object byte-identically to incoming/<basename> (server-side
    S3 copy; object bodies are never read or decoded),
  * writes a deterministic archive copy to archive/<basename> (no wall-clock
    timestamp suffix, unlike the legacy `.YYYYMMDDHHMMSS`),
  * deletes the landed object (parity with the legacy drop-dir rm; the
    size-settle poll is obsolete because S3 emits Object Created only for
    complete objects),
  * quarantines any key not matching CUSTBILL*.dat to quarantine/<basename>,
  * records every object in the DynamoDB batch-state ledger, keyed by
    basename + ETag so EventBridge at-least-once redelivery is a no-op.

Errors always raise: EventBridge retries the invocation and undeliverable
events drain to the rule's dead-letter queue. There are no lock files.
"""

import fnmatch
import json
import os
import uuid

import boto3

VALID_PATTERN = "CUSTBILL*.dat"
LANDING_PREFIX = "landing/"

_s3 = boto3.client("s3")
_ddb = boto3.client("dynamodb")

# Deterministic ledger-id namespace (uuid5, never uuid4).
_LEDGER_NS = uuid.uuid5(uuid.NAMESPACE_URL, "otterworks-tp/aws-ingest")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required environment variable {name} is not set")
    return value


def _ledger_key(basename: str, etag: str) -> dict:
    return {
        "pk": {"S": f"INGEST#{basename}"},
        "sk": {"S": f"OBJECT#{etag}"},
    }


def _ledger_get(table: str, basename: str, etag: str):
    resp = _ddb.get_item(
        TableName=table,
        Key=_ledger_key(basename, etag),
        ConsistentRead=True,
    )
    return resp.get("Item")


def _ledger_put(table: str, basename: str, etag: str, item_fields: dict) -> None:
    # Item content is fully determined by (basename, etag), so a rerun
    # overwrite converges to the identical item.
    item = dict(_ledger_key(basename, etag))
    item["ledger_id"] = {"S": str(uuid.uuid5(_LEDGER_NS, f"{basename}#{etag}"))}
    item.update(item_fields)
    _ddb.put_item(TableName=table, Item=item)


def _copy(bucket: str, source_key: str, dest_key: str) -> None:
    # Server-side copy: bytes are never read or decoded by this function.
    _s3.copy_object(
        Bucket=bucket,
        Key=dest_key,
        CopySource={"Bucket": bucket, "Key": source_key},
        MetadataDirective="COPY",
    )


def handler(event, context):
    bucket = _env("PIPELINE_BUCKET")
    table = _env("BATCH_STATE_TABLE")

    detail = event.get("detail") or {}
    event_bucket = (detail.get("bucket") or {}).get("name")
    key = (detail.get("object") or {}).get("key")
    etag = (detail.get("object") or {}).get("etag", "")
    if not event_bucket or not key:
        raise ValueError(f"malformed event, missing bucket/object: {json.dumps(event)[:512]}")
    if event_bucket != bucket:
        raise ValueError(f"event bucket {event_bucket!r} does not match {bucket!r}")
    if not key.startswith(LANDING_PREFIX):
        raise ValueError(f"key {key!r} is not under {LANDING_PREFIX!r}")

    basename = key[len(LANDING_PREFIX):]
    if not basename or "/" in basename:
        raise ValueError(f"unexpected nested or empty landing key {key!r}")

    if not etag:
        etag = _s3.head_object(Bucket=bucket, Key=key)["ETag"].strip('"')

    valid = fnmatch.fnmatchcase(basename, VALID_PATTERN)
    if valid:
        dispositions = {"incoming": f"incoming/{basename}", "archive": f"archive/{basename}"}
        status = "staged"
    else:
        dispositions = {"quarantine": f"quarantine/{basename}"}
        status = "quarantined"

    already_recorded = _ledger_get(table, basename, etag) is not None
    if already_recorded:
        try:
            _s3.head_object(Bucket=bucket, Key=key)
        except _s3.exceptions.ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                # Redelivery after a completed run: the ledger is written only
                # after staging succeeds and the landed object is gone, so all
                # copies exist. Idempotent no-op.
                return {"status": status, "basename": basename, "redelivery": True}
            raise

    # The landed object still exists: (re)run the server-side copies — they
    # converge to identical destination state — then record and delete.
    for dest in dispositions.values():
        _copy(bucket, key, dest)

    _ledger_put(
        table,
        basename,
        etag,
        {
            "status": {"S": status},
            "source_key": {"S": key},
            "etag": {"S": etag},
            "event_time": {"S": event.get("time", "")},
            **{f"{name}_key": {"S": dest} for name, dest in dispositions.items()},
        },
    )
    _s3.delete_object(Bucket=bucket, Key=key)

    return {
        "status": status,
        "basename": basename,
        "redelivery": already_recorded,
        **{f"{name}_key": dest for name, dest in dispositions.items()},
    }

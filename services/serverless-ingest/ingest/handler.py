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

Errors always raise: Lambda's asynchronous invoke retries and then delivers
the failure to the shared events DLQ (on_failure destination); EventBridge
delivery failures dead-letter to the same queue. Redelivery detection never
depends on S3 404-vs-403 semantics: the DynamoDB ledger is the source of
truth, so the role needs no bucket-wide s3:ListBucket. No lock files.
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


def _ledger_has_basename(table: str, basename: str) -> bool:
    resp = _ddb.query(
        TableName=table,
        KeyConditionExpression="pk = :pk",
        ExpressionAttributeValues={":pk": {"S": f"INGEST#{basename}"}},
        ConsistentRead=True,
        Limit=1,
    )
    return bool(resp.get("Items"))


def _ledger_put(table: str, basename: str, etag: str, item_fields: dict) -> None:
    # Item content is fully determined by (basename, etag), so a rerun
    # overwrite converges to the identical item.
    item = dict(_ledger_key(basename, etag))
    item["ledger_id"] = {"S": str(uuid.uuid5(_LEDGER_NS, f"{basename}#{etag}"))}
    item.update(item_fields)
    _ddb.put_item(TableName=table, Item=item)


# Error codes meaning "the landed object is absent". Without s3:ListBucket
# (deliberately not granted — least privilege) S3 answers 403/AccessDenied for
# a missing key, so both spellings of absence are included; genuinely
# transient or server-side errors (SlowDown, 500, ...) are not.
_ABSENT_CODES = {"404", "NoSuchKey", "403", "AccessDenied"}


def _head_etag(bucket: str, key: str):
    """Return the current ETag of the object, or None if it is absent."""
    try:
        return _s3.head_object(Bucket=bucket, Key=key)["ETag"].strip('"')
    except _s3.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in _ABSENT_CODES:
            return None
        raise


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

    valid = fnmatch.fnmatchcase(basename, VALID_PATTERN)
    if valid:
        dispositions = {"incoming": f"incoming/{basename}", "archive": f"archive/{basename}"}
        status = "staged"
    else:
        dispositions = {"quarantine": f"quarantine/{basename}"}
        status = "quarantined"

    if not etag:
        etag = _head_etag(bucket, key)
        if etag is None:
            # The landed object is gone. If a ledger row for this basename
            # exists, a prior run completed and this is a redelivery: no-op.
            # Otherwise the object genuinely vanished — surface the error.
            if _ledger_has_basename(table, basename):
                return {"status": status, "basename": basename, "redelivery": True}
            raise RuntimeError(f"landed object {key!r} not found and no ledger record exists")

    if _ledger_get(table, basename, etag) is not None:
        # Redelivery of a recorded object: copies and ledger row already
        # converged. Only the landing delete may remain if the prior run
        # crashed between ledger put and delete. Delete only if the object
        # at the key is still the recorded one: feed names are reused, so a
        # replayed old event must never remove a newer same-named object
        # (that object's own event processes it).
        if _head_etag(bucket, key) == etag:
            _s3.delete_object(Bucket=bucket, Key=key)
        return {"status": status, "basename": basename, "redelivery": True}

    # (Re)run the server-side copies — they converge to identical
    # destination state — then record and delete.
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
    # Guarded delete, mirroring the redelivery branch: if a newer same-named
    # object has overwritten the key since the copy, leave it for its own
    # event rather than destroying it.
    if _head_etag(bucket, key) == etag:
        _s3.delete_object(Bucket=bucket, Key=key)

    return {
        "status": status,
        "basename": basename,
        "redelivery": False,
        **{f"{name}_key": dest for name, dest in dispositions.items()},
    }

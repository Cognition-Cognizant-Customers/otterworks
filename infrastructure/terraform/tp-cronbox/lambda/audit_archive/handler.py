"""Expiry-driven audit archival for ow-tp-audit-events.

Replaces the legacy weekly cron job etl/scripts/audit_archive_weekly.py. There is
no schedule: DynamoDB TTL decides when an item expires, and this function
persists the item to S3 before the expiry deletes it.

Two entry paths, one archive writer:

* ``stream`` — DynamoDB Streams REMOVE records whose ``userIdentity`` marks them
  as TTL deletions. The old image is archived before the item is lost.
* ``sweep`` — an on-demand reconciliation invoke (no cron rule, no scheduled
  event). It archives every item whose ``expires_at`` already lies strictly
  before the reference instant, so an item is durable in S3 well before TTL
  physically removes it (TTL deletes on a best-effort basis, typically within
  48 hours).

Archive keys are a pure function of the item's composite key, so re-running
either path converges: an item already archived is skipped, never duplicated.
"""

import base64
import decimal
import gzip
import io
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "90"))
TABLE_NAME = os.environ.get("TABLE_NAME", "ow-tp-audit-events")
ARCHIVE_BUCKET = os.environ.get("ARCHIVE_BUCKET", "ow-tp-audit-archive")
ARCHIVE_PREFIX = os.environ.get("ARCHIVE_PREFIX", "audit-archive/expired")
TTL_ATTRIBUTE = os.environ.get("TTL_ATTRIBUTE", "expires_at")
METRIC_NAMESPACE = os.environ.get("METRIC_NAMESPACE", "ow-tp-audit-archive")
ENDPOINT_URL = os.environ.get("AWS_ENDPOINT_URL") or None

TTL_PRINCIPALS = ("dynamodb.amazonaws.com",)


def _client(service):
    return boto3.client(service, endpoint_url=ENDPOINT_URL)


class ArchiveIncomplete(RuntimeError):
    """A sweep archived everything it could, but not everything it had to."""

    def __init__(self, result):
        super().__init__(f"sweep could not archive: {result['failed']}")
        self.result = result


class ArchiveEncoder(json.JSONEncoder):
    """Archive DynamoDB values without coercing or reordering them."""

    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        if isinstance(o, (bytes, bytearray)):
            return {"$binary_base64": base64.b64encode(bytes(o)).decode("ascii")}
        if isinstance(o, set):
            return sorted(o, key=repr)
        return super().default(o)


def deserialize(image):
    """Convert a low-level DynamoDB AttributeValue map to plain Python."""
    return {name: _value(value) for name, value in image.items()}


def _value(value):
    (tag, raw), = value.items()
    if tag == "S":
        return raw
    if tag == "N":
        return decimal.Decimal(raw)
    if tag == "BOOL":
        return bool(raw)
    if tag == "NULL":
        return None
    if tag == "B":
        return raw if isinstance(raw, bytes) else base64.b64decode(raw)
    if tag == "SS":
        return sorted(raw)
    if tag == "NS":
        return sorted(decimal.Decimal(item) for item in raw)
    if tag == "BS":
        return [item if isinstance(item, bytes) else base64.b64decode(item) for item in raw]
    if tag == "L":
        return [_value(item) for item in raw]
    if tag == "M":
        return {name: _value(item) for name, item in raw.items()}
    raise ValueError(f"unsupported DynamoDB attribute type: {tag}")


def archive_key(record):
    """Deterministic, collision-free key derived from the composite table key."""
    stamp = str(record["timestamp"]).replace("-", "").replace(":", "")
    expiry = record.get(TTL_ATTRIBUTE)
    if expiry is None:
        partition = "unknown"
    else:
        partition = datetime.fromtimestamp(int(expiry), tz=timezone.utc).strftime("%Y-%m-%d")
    return "{}/dt={}/{}__{}.jsonl.gz".format(
        ARCHIVE_PREFIX,
        partition,
        record["event_id"],
        stamp,
    )


def encode(record):
    """One JSONL.gz member per record, gzip mtime=0 so bytes are stable."""
    line = json.dumps(record, cls=ArchiveEncoder, sort_keys=True, ensure_ascii=False)
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(line.encode("utf-8"))
        gz.write(b"\n")
    return buf.getvalue()


def expiry_of(record):
    """None when the item carries no usable TTL attribute (never expirable)."""
    raw = record.get(TTL_ATTRIBUTE)
    if raw is None:
        return None
    try:
        return int(decimal.Decimal(str(raw)))
    except (ValueError, ArithmeticError):
        return None


def put_archive(s3, record):
    """Write the record unless its key already exists. Returns (key, written)."""
    key = archive_key(record)
    try:
        s3.head_object(Bucket=ARCHIVE_BUCKET, Key=key)
        return key, False
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
            raise
    s3.put_object(Bucket=ARCHIVE_BUCKET, Key=key, Body=encode(record))
    return key, True


def emit_metrics(counts):
    try:
        _client("cloudwatch").put_metric_data(
            Namespace=METRIC_NAMESPACE,
            MetricData=[
                {"MetricName": name, "Value": float(value), "Unit": "Count"}
                for name, value in counts.items()
            ],
        )
    except ClientError as error:  # metrics must never fail the archive path
        print(f"metric publish failed: {error}")


def is_ttl_removal(record):
    identity = record.get("userIdentity") or {}
    return record.get("eventName") == "REMOVE" and identity.get("principalId") in TTL_PRINCIPALS


def handle_stream(event):
    """Archive TTL removals, reporting per-record failures.

    The item is already gone from the table when its removal reaches the stream,
    so a failed write must not discard the rest of the batch: each failure is
    reported by sequence number (``ReportBatchItemFailures``) and retried until
    it succeeds or the stream's own retention expires.
    """
    s3 = _client("s3")
    archived, already, skipped, failures = [], [], [], []
    for record in event.get("Records", []):
        image = (record.get("dynamodb") or {}).get("OldImage")
        if not is_ttl_removal(record) or not image:
            skipped.append(record.get("eventID"))
            continue
        try:
            item = deserialize(image)
            _, written = put_archive(s3, item)
        except Exception as error:  # noqa: BLE001 - one bad record must not drop the batch
            sequence = (record.get("dynamodb") or {}).get("SequenceNumber")
            print(f"archive failed for sequence {sequence}: {error}")
            failures.append({"itemIdentifier": sequence})
            continue
        (archived if written else already).append(item["event_id"])
    result = {
        "mode": "stream",
        "archived": sorted(archived),
        "already_archived": sorted(already),
        "skipped_non_ttl": [item for item in skipped if item],
        "batchItemFailures": failures,
    }
    emit_metrics(
        {
            "ArchivedRecords": len(archived),
            "AlreadyArchivedRecords": len(already),
            "SkippedNonTtlRemovals": len(result["skipped_non_ttl"]),
            "FailedArchiveRecords": len(failures),
        }
    )
    return result


def scan_table(dynamodb):
    kwargs = {"TableName": TABLE_NAME}
    while True:
        page = dynamodb.scan(**kwargs)
        for image in page.get("Items", []):
            yield deserialize(image)
        last = page.get("LastEvaluatedKey")
        if not last:
            return
        kwargs["ExclusiveStartKey"] = last


def reference_epoch(event):
    reference = event.get("reference_time")
    if reference:
        parsed = datetime.fromisoformat(reference.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    return int(datetime.now(tz=timezone.utc).timestamp())


def handle_sweep(event):
    """Archive everything already expired at the reference instant.

    The comparison is strictly less-than, which is exactly the legacy
    ``timestamp < run_date - 90d`` test shifted by the retention horizon:
    ``expires_at = timestamp + 90d``, so ``expires_at < reference`` selects the
    same records and keeps the cutoff exclusive.
    """
    dynamodb = _client("dynamodb")
    s3 = _client("s3")
    cutoff = reference_epoch(event)
    archived, already, retained, unexpirable, keys = [], [], [], [], {}
    failed = []
    for item in scan_table(dynamodb):
        expiry = expiry_of(item)
        if expiry is None:
            # Never silently expire an item we cannot reason about: keep it and
            # make it visible instead.
            unexpirable.append(item["event_id"])
            continue
        if expiry >= cutoff:
            retained.append(item["event_id"])
            continue
        try:
            key, written = put_archive(s3, item)
        except Exception as error:  # noqa: BLE001 - archive every other expiring item
            print(f"archive failed for {item.get('event_id')}: {error}")
            failed.append(item.get("event_id"))
            continue
        keys[item["event_id"]] = key
        (archived if written else already).append(item["event_id"])
    result = {
        "mode": "sweep",
        "reference_epoch": cutoff,
        "retention_days": RETENTION_DAYS,
        "table": TABLE_NAME,
        "archive_bucket": ARCHIVE_BUCKET,
        "archived": sorted(archived),
        "already_archived": sorted(already),
        "retained": sorted(retained),
        "unexpirable": sorted(unexpirable),
        "failed": sorted(item for item in failed if item),
        "archive_keys": keys,
    }
    emit_metrics(
        {
            "ArchivedRecords": len(archived),
            "AlreadyArchivedRecords": len(already),
            "UnexpirableRecords": len(unexpirable),
            "FailedArchiveRecords": len(failed),
        }
    )
    if failed:
        # Every archivable item was archived first; a partial sweep must still be
        # a failed invoke so it is never mistaken for full coverage.
        raise ArchiveIncomplete(result)
    return result


def lambda_handler(event, context=None):
    event = event or {}
    if event.get("Records"):
        return handle_stream(event)
    if event.get("mode", "sweep") == "sweep":
        return handle_sweep(event)
    raise ValueError("unsupported event: expected DynamoDB stream records or mode=sweep")

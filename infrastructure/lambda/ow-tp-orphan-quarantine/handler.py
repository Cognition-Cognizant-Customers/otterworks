"""Event-driven orphan quarantine for the Cron Box file estate."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError


def _client_kwargs() -> dict:
    kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
    endpoint = os.getenv("AWS_ENDPOINT_URL")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
        kwargs["aws_access_key_id"] = os.getenv("AWS_ACCESS_KEY_ID", "123456789012")
        kwargs["aws_secret_access_key"] = os.getenv(
            "AWS_SECRET_ACCESS_KEY", "cronbox-local-secret"
        )
    return kwargs


_clients = _client_kwargs()
s3 = boto3.client("s3", **_clients)
dynamodb = boto3.resource("dynamodb", **_clients)


def _setting(name: str, default: str) -> str:
    return os.getenv(name, default)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: str | None) -> datetime:
    if not value:
        return _now()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _is_not_found(error: ClientError) -> bool:
    response = error.response
    return (
        response.get("Error", {}).get("Code")
        in {
            "404",
            "NoSuchKey",
            "NotFound",
        }
        or response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404
    )


def _head(bucket: str, key: str) -> dict | None:
    try:
        return s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if _is_not_found(error):
            return None
        raise


def _resolve_source(raw_key: str) -> tuple[str, dict] | None:
    metadata = _head(_setting("STORAGE_BUCKET", "ow-tp-file-storage"), raw_key)
    if metadata is not None:
        return raw_key, metadata
    decoded_key = unquote_plus(raw_key)
    if decoded_key != raw_key:
        metadata = _head(_setting("STORAGE_BUCKET", "ow-tp-file-storage"), decoded_key)
        if metadata is not None:
            return decoded_key, metadata
    return None


def _audit_table():
    return dynamodb.Table(_setting("AUDIT_TABLE", "ow-tp-orphan-audit"))


def _get_audit(key: str) -> dict | None:
    response = _audit_table().get_item(Key={"object_key": key}, ConsistentRead=True)
    return response.get("Item")


def _quarantine_exists(key: str) -> bool:
    return (
        _head(_setting("QUARANTINE_BUCKET", "ow-tp-file-quarantine"), key) is not None
    )


def _metadata_snapshot() -> tuple[list[str], int]:
    table = dynamodb.Table(_setting("METADATA_TABLE", "ow-tp-file-metadata"))
    references: list[str] = []
    unresolvable = 0
    scan_kwargs = {"ProjectionExpression": "s3_key"}
    while True:
        page = table.scan(**scan_kwargs)
        for item in page.get("Items", []):
            key = item.get("s3_key")
            if isinstance(key, str) and key:
                references.append(key)
            else:
                unresolvable += 1
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            return references, unresolvable
        scan_kwargs["ExclusiveStartKey"] = last_key


def _list_objects() -> list[dict]:
    objects: list[dict] = []
    kwargs = {
        "Bucket": _setting("STORAGE_BUCKET", "ow-tp-file-storage"),
        "Prefix": _setting("FILES_PREFIX", "files/"),
    }
    while True:
        page = s3.list_objects_v2(**kwargs)
        objects.extend(page.get("Contents", []))
        if not page.get("IsTruncated"):
            return objects
        kwargs["ContinuationToken"] = page["NextContinuationToken"]


def _remaining_seconds(context) -> float | None:
    if context is None or not hasattr(context, "get_remaining_time_in_millis"):
        return None
    return max(0.0, context.get_remaining_time_in_millis() / 1000 - 0.1)


def _recheck_if_young(metadata: dict, context) -> None:
    delay = float(_setting("RECHECK_DELAY_SECONDS", "45"))
    if delay <= 0:
        return
    modified = metadata.get("LastModified")
    if not modified:
        return
    if modified.tzinfo is None:
        modified = modified.replace(tzinfo=timezone.utc)
    age = max(0.0, (_now() - modified.astimezone(timezone.utc)).total_seconds())
    remaining_delay = delay - age
    if remaining_delay <= 0:
        return
    available = _remaining_seconds(context)
    wait_seconds = (
        remaining_delay if available is None else min(remaining_delay, available)
    )
    if wait_seconds > 0:
        time.sleep(wait_seconds)


def _audit_item(
    *,
    object_key: str,
    decision: str,
    detected_at: str,
    trigger_source: str,
    unresolvable_references: int,
    size_bytes: int = 0,
    quarantine_key: str | None = None,
    freed_bytes: int = 0,
) -> dict:
    item = {
        "object_key": object_key,
        "decision": decision,
        "detected_at": detected_at,
        "trigger_source": trigger_source,
        "unresolvable_references": unresolvable_references,
        "size_bytes": size_bytes,
        "freed_bytes": freed_bytes,
    }
    if quarantine_key is not None:
        item["quarantine_key"] = quarantine_key
    return item


def _put_audit(item: dict) -> None:
    try:
        _audit_table().put_item(
            Item=item,
            ConditionExpression=(
                "attribute_not_exists(object_key) OR decision <> :quarantined"
            ),
            ExpressionAttributeValues={":quarantined": "quarantined"},
        )
    except ClientError as error:
        if (
            error.response.get("Error", {}).get("Code")
            != "ConditionalCheckFailedException"
        ):
            raise


def _already_processed(source_key: str) -> dict | None:
    item = _get_audit(source_key)
    if not item:
        return None
    decision = item.get("decision")
    quarantine_key = item.get("quarantine_key")
    if (
        decision == "quarantined"
        and quarantine_key
        and _quarantine_exists(quarantine_key)
    ):
        return {
            "object_key": source_key,
            "status": "already_quarantined",
            "quarantine_key": quarantine_key,
        }
    return None


def _quarantine(
    source_key: str,
    metadata: dict,
    detected_at: datetime,
    trigger_source: str,
    unresolvable_references: int,
    context,
    audit_detected_at: str | None = None,
) -> dict:
    storage_bucket = _setting("STORAGE_BUCKET", "ow-tp-file-storage")
    quarantine_bucket = _setting("QUARANTINE_BUCKET", "ow-tp-file-quarantine")
    quarantine_prefix = _setting("QUARANTINE_PREFIX", "quarantined").rstrip("/")
    quarantine_key = f"{quarantine_prefix}/{detected_at:%Y-%m-%d}/{source_key}"
    size_bytes = int(metadata.get("ContentLength", 0))

    s3.copy_object(
        Bucket=quarantine_bucket,
        Key=quarantine_key,
        CopySource={"Bucket": storage_bucket, "Key": source_key},
        MetadataDirective="COPY",
    )
    s3.delete_object(Bucket=storage_bucket, Key=source_key)
    item = _audit_item(
        object_key=source_key,
        decision="quarantined",
        detected_at=audit_detected_at or _iso(detected_at),
        trigger_source=trigger_source,
        unresolvable_references=unresolvable_references,
        size_bytes=size_bytes,
        quarantine_key=quarantine_key,
        freed_bytes=size_bytes,
    )
    _put_audit(item)
    return {
        "object_key": source_key,
        "status": "quarantined",
        "quarantine_key": quarantine_key,
        "size_bytes": size_bytes,
        "freed_bytes": size_bytes,
        "unresolvable_references": unresolvable_references,
    }


def _process_object(
    raw_key: str,
    detected_at: datetime,
    trigger_source: str,
    context=None,
    metadata_snapshot: tuple[list[str], int] | None = None,
) -> dict:
    existing = _already_processed(raw_key)
    if existing:
        return existing

    resolved = _resolve_source(raw_key)
    if resolved is None:
        return {"object_key": raw_key, "status": "source_not_found"}
    source_key, metadata = resolved
    if source_key != raw_key:
        existing = _already_processed(source_key)
        if existing:
            return existing

    existing_audit = _get_audit(source_key)
    retained_detected_at = (
        existing_audit.get("detected_at")
        if existing_audit and existing_audit.get("decision") == "retained"
        else None
    )
    references, unresolvable = metadata_snapshot or _metadata_snapshot()
    if source_key in references:
        item = _audit_item(
            object_key=source_key,
            decision="retained",
            detected_at=retained_detected_at or _iso(detected_at),
            trigger_source=trigger_source,
            unresolvable_references=unresolvable,
            size_bytes=int(metadata.get("ContentLength", 0)),
        )
        _put_audit(item)
        return {
            "object_key": source_key,
            "status": "retained",
            "size_bytes": int(metadata.get("ContentLength", 0)),
            "freed_bytes": 0,
            "unresolvable_references": unresolvable,
        }

    if trigger_source == "event" and _setting("RECHECK_DELAY_SECONDS", "45") != "0":
        _recheck_if_young(metadata, context)
        refreshed = _resolve_source(source_key)
        if refreshed is None:
            return {"object_key": source_key, "status": "source_not_found"}
        _, metadata = refreshed
        references, unresolvable = _metadata_snapshot()
        if source_key in references:
            item = _audit_item(
                object_key=source_key,
                decision="retained",
                detected_at=retained_detected_at or _iso(detected_at),
                trigger_source=trigger_source,
                unresolvable_references=unresolvable,
                size_bytes=int(metadata.get("ContentLength", 0)),
            )
            _put_audit(item)
            return {
                "object_key": source_key,
                "status": "retained",
                "size_bytes": int(metadata.get("ContentLength", 0)),
                "freed_bytes": 0,
                "unresolvable_references": unresolvable,
            }

    return _quarantine(
        source_key,
        metadata,
        detected_at,
        trigger_source,
        unresolvable,
        context,
        retained_detected_at,
    )


def _event_key(event: dict) -> str:
    try:
        return event["detail"]["object"]["key"]
    except (KeyError, TypeError) as error:
        raise ValueError("event is missing detail.object.key") from error


def _handle_event(event: dict, context) -> dict:
    detected_at = _parse_time(event.get("time"))
    return _process_object(_event_key(event), detected_at, "event", context)


def _handle_reconcile(context) -> dict:
    detected_at = _now()
    objects = _list_objects()
    references, unresolvable = _metadata_snapshot()
    object_keys = {item["Key"] for item in objects}
    reverse_orphans = sum(1 for key in references if key not in object_keys)
    results = []
    for obj in objects:
        results.append(
            _process_object(
                obj["Key"],
                detected_at,
                "sweep",
                context,
                (references, unresolvable),
            )
        )

    counts = {
        "quarantined": sum(
            1 for result in results if result["status"] == "quarantined"
        ),
        "freed_bytes": sum(
            int(result.get("freed_bytes", 0))
            for result in results
            if result["status"] == "quarantined"
        ),
    }
    summary_key = f"__sweep__/{detected_at.isoformat(timespec='microseconds').replace('+00:00', 'Z')}"
    summary = {
        "object_key": summary_key,
        "decision": "sweep_summary",
        "detected_at": _iso(detected_at),
        "trigger_source": "sweep",
        "objects_scanned": len(objects),
        "referenced_objects": sum(1 for key in object_keys if key in references),
        "orphans_found": sum(1 for key in object_keys if key not in references),
        "orphans_quarantined": counts["quarantined"],
        "freed_bytes": counts["freed_bytes"],
        "unresolvable_references": unresolvable,
        "reverse_orphans": reverse_orphans,
    }
    summary["status"] = "reconciled"
    _put_audit(summary)
    return summary


def handler(event, context):
    if isinstance(event, dict) and event.get("mode") == "reconcile":
        return _handle_reconcile(context)
    return _handle_event(event, context)

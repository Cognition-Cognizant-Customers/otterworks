"""Lambda tasks for the otterworks-storage-cleanup state machine.

State machine flow:
  list_s3_objects -> list_metadata_references
    -> find_orphaned_objects -> move_to_quarantine -> generate_storage_report

The two scans are deliberately sequential, with the S3 listing first, and
find_orphans additionally skips objects newer than MIN_ORPHAN_AGE_HOURS, so
an in-flight upload is never misclassified as an orphan (a DynamoDB scan is
not a point-in-time snapshot, so scan ordering alone is not sufficient).
"""

import json
from datetime import UTC, datetime, timedelta

from otterworks_etl.common.config import client, env, resource
from otterworks_etl.common.dispatch import make_handler
from otterworks_etl.common.logging import get_logger
from otterworks_etl.common.staging import read_staged, write_staged

logger = get_logger(__name__)

PIPELINE = "storage-cleanup"
FILES_PREFIX = "files/"
QUARANTINE_PREFIX = "quarantined"
S3_STANDARD_PRICE_PER_GB = 0.023
# objects newer than this are never quarantined: a DynamoDB scan is not a
# point-in-time snapshot, so a just-uploaded object's metadata row can be
# missed even with the S3-listing-first ordering
MIN_ORPHAN_AGE_HOURS = 24


def _ds(event: dict) -> str:
    return (event.get("ds") or datetime.now(tz=UTC).isoformat())[:10]


def find_orphans(
    objects: list[dict],
    referenced_keys: set[str],
    now: datetime | None = None,
) -> tuple[list[dict], int]:
    now = now or datetime.now(tz=UTC)
    age_cutoff = now - timedelta(hours=MIN_ORPHAN_AGE_HOURS)
    orphaned = []
    for obj in objects:
        if obj["key"] in referenced_keys:
            continue
        last_modified = obj.get("last_modified")
        if last_modified and datetime.fromisoformat(last_modified) > age_cutoff:
            continue
        orphaned.append(obj)
    return orphaned, sum(obj["size"] for obj in orphaned)


def list_s3_objects(event: dict) -> dict:
    bucket = env("FILE_STORAGE_BUCKET")
    paginator = client("s3").get_paginator("list_objects_v2")

    objects = []
    for page in paginator.paginate(Bucket=bucket, Prefix=FILES_PREFIX):
        for obj in page.get("Contents", []):
            objects.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })

    key = write_staged(PIPELINE, event["execution_id"], "s3_objects", objects)
    return {
        "staged_key": key,
        "total_objects": len(objects),
        "total_size_bytes": sum(o["size"] for o in objects),
    }


def list_metadata_references(event: dict) -> dict:
    table = resource("dynamodb").Table(env("FILE_METADATA_TABLE"))

    referenced = set()
    scan_kwargs = {"ProjectionExpression": "s3_key"}
    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            if item.get("s3_key"):
                referenced.add(item["s3_key"])
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    key = write_staged(
        PIPELINE, event["execution_id"], "referenced_keys", sorted(referenced)
    )
    return {"staged_key": key, "referenced_count": len(referenced)}


def find_orphaned_objects(event: dict) -> dict:
    inventory = event["inventory"]
    references = event["references"]
    objects = read_staged(inventory["staged_key"])
    referenced_keys = set(read_staged(references["staged_key"]))

    orphaned, orphaned_bytes = find_orphans(objects, referenced_keys)

    key = write_staged(PIPELINE, event["execution_id"], "orphaned", orphaned)
    return {
        "staged_key": key,
        "orphaned_count": len(orphaned),
        "orphaned_bytes": orphaned_bytes,
        "total_objects": inventory["total_objects"],
        "total_size_bytes": inventory["total_size_bytes"],
    }


def move_to_quarantine(event: dict) -> dict:
    ds = _ds(event)
    orphaned = read_staged(event["orphans"]["staged_key"])
    file_bucket = env("FILE_STORAGE_BUCKET")
    quarantine_bucket = env("QUARANTINE_BUCKET")
    s3 = client("s3")

    moved = 0
    failed = 0
    for obj in orphaned:
        source_key = obj["key"]
        dest_key = f"{QUARANTINE_PREFIX}/{ds}/{source_key}"
        try:
            s3.copy_object(
                Bucket=quarantine_bucket,
                Key=dest_key,
                CopySource={"Bucket": file_bucket, "Key": source_key},
                MetadataDirective="COPY",
            )
            s3.delete_object(Bucket=file_bucket, Key=source_key)
            moved += 1
        except Exception:
            logger.exception(
                "failed to quarantine object",
                extra={"context": {"key": source_key}},
            )
            failed += 1

    return {"moved_count": moved, "failed_count": failed}


def generate_storage_report(event: dict) -> dict:
    ds = _ds(event)
    orphans = event["orphans"]
    cleanup = event["cleanup"]

    savings_gb = orphans["orphaned_bytes"] / (1024 ** 3)
    total_objects = orphans["total_objects"]

    report = {
        "report_type": "storage_cleanup",
        "report_date": ds,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "inventory": {
            "total_objects": total_objects,
            "total_size_bytes": orphans["total_size_bytes"],
            "total_size_gb": round(orphans["total_size_bytes"] / (1024 ** 3), 4),
        },
        "orphans": {
            "orphaned_objects": orphans["orphaned_count"],
            "orphaned_bytes": orphans["orphaned_bytes"],
            "orphaned_size_gb": round(savings_gb, 4),
            "orphan_percentage": round(
                (orphans["orphaned_count"] / total_objects * 100) if total_objects else 0,
                2,
            ),
        },
        "cleanup": {
            "objects_quarantined": cleanup["moved_count"],
            "objects_failed": cleanup["failed_count"],
            "quarantine_bucket": env("QUARANTINE_BUCKET"),
        },
        "savings": {
            "storage_freed_gb": round(savings_gb, 4),
            "estimated_monthly_savings_usd": round(
                savings_gb * S3_STANDARD_PRICE_PER_GB, 4
            ),
        },
    }

    report_key = f"reports/storage-cleanup/{ds}/report.json"
    client("s3").put_object(
        Bucket=env("DATA_LAKE_BUCKET"),
        Key=report_key,
        Body=json.dumps(report, indent=2).encode("utf-8"),
    )
    return {"report_key": report_key, "objects_quarantined": cleanup["moved_count"]}


handler = make_handler(PIPELINE, {
    "list_s3_objects": list_s3_objects,
    "list_metadata_references": list_metadata_references,
    "find_orphaned_objects": find_orphaned_objects,
    "move_to_quarantine": move_to_quarantine,
    "generate_storage_report": generate_storage_report,
})

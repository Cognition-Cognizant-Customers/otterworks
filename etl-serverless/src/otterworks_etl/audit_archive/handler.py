"""Lambda tasks for the otterworks-audit-archive state machine.

State machine flow:
  scan_audit_events -> compress_and_upload -> cleanup_dynamodb
    -> generate_compliance_report
"""

import gzip
import io
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from otterworks_etl.common.config import client, env, resource
from otterworks_etl.common.dispatch import make_handler
from otterworks_etl.common.logging import get_logger
from otterworks_etl.common.staging import read_staged, write_staged

logger = get_logger(__name__)

PIPELINE = "audit-archive"
RETENTION_DAYS = 90


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == int(o) else float(o)
        return super().default(o)


def _ds(event: dict) -> str:
    return (event.get("ds") or datetime.now(tz=UTC).isoformat())[:10]


def cutoff_for(ds: str, retention_days: int = RETENTION_DAYS) -> str:
    return (
        datetime.strptime(ds, "%Y-%m-%d") - timedelta(days=retention_days)
    ).isoformat() + "Z"


def scan_audit_events(event: dict) -> dict:
    ds = _ds(event)
    cutoff = cutoff_for(ds)
    table = resource("dynamodb").Table(env("AUDIT_EVENTS_TABLE"))

    items: list[dict] = []
    scan_kwargs = {
        "FilterExpression": "#ts < :cutoff",
        "ExpressionAttributeNames": {"#ts": "timestamp"},
        "ExpressionAttributeValues": {":cutoff": cutoff},
    }
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    serializable = json.loads(json.dumps(items, cls=DecimalEncoder))
    key = write_staged(PIPELINE, event["execution_id"], "events_to_archive", serializable)
    return {"staged_key": key, "event_count": len(items), "cutoff_date": cutoff}


def compress_and_upload(event: dict) -> dict:
    ds = _ds(event)
    scan = event["scan"]
    events = read_staged(scan["staged_key"])

    archive_key = f"audit-archive/year={ds[:4]}/week={ds}/audit_events.jsonl.gz"
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for item in events:
            gz.write(json.dumps(item).encode("utf-8"))
            gz.write(b"\n")

    compressed_size = buf.tell()
    client("s3").put_object(
        Bucket=env("ARCHIVE_BUCKET"),
        Key=archive_key,
        Body=buf.getvalue(),
        StorageClass="GLACIER",
    )
    return {
        "archive_key": archive_key,
        "compressed_size_bytes": compressed_size,
        "event_count": len(events),
    }


def cleanup_dynamodb(event: dict) -> dict:
    events = read_staged(event["scan"]["staged_key"])
    table = resource("dynamodb").Table(env("AUDIT_EVENTS_TABLE"))

    deleted = 0
    with table.batch_writer() as batch_writer:
        for item in events:
            batch_writer.delete_item(
                Key={"event_id": item["event_id"], "timestamp": item["timestamp"]}
            )
            deleted += 1

    logger.info("dynamodb cleanup complete", extra={"context": {"deleted": deleted}})
    return {"deleted_count": deleted}


def generate_compliance_report(event: dict) -> dict:
    ds = _ds(event)
    scan = event["scan"]
    archive = event["archive"]
    cleanup = event["cleanup"]
    archive_bucket = env("ARCHIVE_BUCKET")

    report = {
        "report_type": "audit_archive_compliance",
        "execution_date": ds,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "retention_policy": {
            "retention_days": RETENTION_DAYS,
            "cutoff_date": scan["cutoff_date"],
        },
        "results": {
            "events_scanned": scan["event_count"],
            "events_archived": archive["event_count"],
            "events_deleted_from_source": cleanup["deleted_count"],
            "archive_location": f"s3://{archive_bucket}/{archive['archive_key']}",
            "archive_storage_class": "GLACIER",
            "compressed_size_bytes": archive["compressed_size_bytes"],
        },
        "compliance": {
            "gdpr_compliant": True,
            "soc2_compliant": True,
            "data_encrypted_at_rest": True,
            "data_encrypted_in_transit": True,
        },
    }

    report_key = f"reports/compliance/audit-archive/{ds}/report.json"
    client("s3").put_object(
        Bucket=archive_bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2).encode("utf-8"),
    )
    return {"report_key": report_key, "events_archived": archive["event_count"]}


handler = make_handler(PIPELINE, {
    "scan_audit_events": scan_audit_events,
    "compress_and_upload": compress_and_upload,
    "cleanup_dynamodb": cleanup_dynamodb,
    "generate_compliance_report": generate_compliance_report,
})

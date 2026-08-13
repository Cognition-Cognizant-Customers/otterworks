"""Lambda tasks for the otterworks-analytics-etl state machine.

State machine flow:
  extract_from_sqs + extract_from_dynamodb (parallel)
    -> transform_events
    -> load_to_data_lake + update_postgres_aggregates (parallel)
    -> generate_report
"""

import gzip
import io
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from otterworks_etl.analytics.transform import aggregate_events, peak_hour
from otterworks_etl.common.config import client, env, resource
from otterworks_etl.common.db import pg_connection
from otterworks_etl.common.dispatch import make_handler
from otterworks_etl.common.logging import get_logger
from otterworks_etl.common.staging import list_staged, read_staged, write_staged

logger = get_logger(__name__)

PIPELINE = "analytics"
MAX_SQS_MESSAGES = 10000
SQS_BATCH_SIZE = 10

UPSERT_SQL = """
    INSERT INTO analytics_daily_summary (
        report_date, active_users, active_documents, active_files,
        total_events, documents_created, documents_edited,
        comments_added, files_uploaded, files_shared,
        files_deleted, bytes_uploaded, updated_at
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
    ON CONFLICT (report_date) DO UPDATE SET
        active_users = EXCLUDED.active_users,
        active_documents = EXCLUDED.active_documents,
        active_files = EXCLUDED.active_files,
        total_events = EXCLUDED.total_events,
        documents_created = EXCLUDED.documents_created,
        documents_edited = EXCLUDED.documents_edited,
        comments_added = EXCLUDED.comments_added,
        files_uploaded = EXCLUDED.files_uploaded,
        files_shared = EXCLUDED.files_shared,
        files_deleted = EXCLUDED.files_deleted,
        bytes_uploaded = EXCLUDED.bytes_uploaded,
        updated_at = NOW();
"""


def _ds(event: dict) -> str:
    return (event.get("ds") or datetime.now(tz=UTC).isoformat())[:10]


def _normalize_decimals(item: dict) -> dict:
    for key, value in item.items():
        if isinstance(value, Decimal):
            item[key] = int(value) if value == int(value) else float(value)
    return item


def extract_from_sqs(event: dict) -> dict:
    ds = _ds(event)
    queue_url = env("ANALYTICS_QUEUE_URL")
    dlq_url = env("ANALYTICS_DLQ_URL")
    sqs = client("sqs")

    # unique per invocation so a retried attempt never overwrites chunks
    # staged (and already deleted from the queue) by a previous attempt
    attempt = uuid.uuid4().hex
    event_count = 0
    malformed = 0
    processed = 0
    chunk = 0

    while processed < MAX_SQS_MESSAGES:
        response = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=SQS_BATCH_SIZE,
            WaitTimeSeconds=2,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )
        messages = response.get("Messages", [])
        if not messages:
            break

        batch_events: list[dict] = []
        entries_to_delete = []
        for msg in messages:
            try:
                # keep the message id with the event so a batch that was
                # staged but not deleted before a crash (and therefore staged
                # again by the retry) is deduplicated in transform_events
                batch_events.append(
                    {"message_id": msg["MessageId"], "event": json.loads(msg["Body"])}
                )
            except (json.JSONDecodeError, KeyError):
                # route malformed payloads to the DLQ instead of dropping
                # them; SQS rejects an empty MessageBody, so fall back to an
                # envelope when the body itself is missing
                body = msg.get("Body") or json.dumps(
                    {"malformed_message": msg.get("MessageId")}
                )
                sqs.send_message(QueueUrl=dlq_url, MessageBody=body)
                malformed += 1
            entries_to_delete.append(
                {"Id": msg["MessageId"], "ReceiptHandle": msg["ReceiptHandle"]}
            )

        # stage this batch durably before deleting it from the queue, so a
        # retry after a mid-run failure never loses already-consumed events
        if batch_events:
            # staged under the report date rather than the execution id: the
            # queue drain is destructive, so a re-execution for the same ds
            # must be able to reuse events consumed by earlier executions
            write_staged(
                PIPELINE,
                f"ds={ds}",
                f"sqs_events/{attempt}_{chunk:05d}",
                batch_events,
            )
            chunk += 1
            event_count += len(batch_events)
        sqs.delete_message_batch(QueueUrl=queue_url, Entries=entries_to_delete)
        processed += len(messages)

    # list the whole prefix so chunks staged by earlier attempts and earlier
    # executions for this ds are still included downstream
    staged_keys = list_staged(PIPELINE, f"ds={ds}", "sqs_events/")
    logger.info("sqs extract complete", extra={"context": {
        "events": event_count, "malformed": malformed, "staged_chunks": len(staged_keys)}})
    return {"staged_keys": staged_keys, "event_count": event_count, "malformed_count": malformed}


def extract_from_dynamodb(event: dict) -> dict:
    ds = _ds(event)
    table = resource("dynamodb").Table(env("ANALYTICS_EVENTS_TABLE"))

    items: list[dict] = []
    scan_kwargs = {
        "FilterExpression": "begins_with(event_date, :ds)",
        "ExpressionAttributeValues": {":ds": ds},
    }
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(_normalize_decimals(item) for item in response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    key = write_staged(PIPELINE, event["execution_id"], "dynamo_events", items)
    logger.info("dynamodb extract complete", extra={"context": {"events": len(items)}})
    return {"staged_key": key, "event_count": len(items)}


def transform_events(event: dict) -> dict:
    execution_id = event["execution_id"]
    all_events: list[dict] = []
    seen_message_ids: set[str] = set()
    for extract in event["extracts"]:
        keys = extract["staged_keys"] if "staged_keys" in extract else [extract["staged_key"]]
        for key in keys:
            for record in read_staged(key):
                # SQS records carry their message id so events staged twice by
                # a retried extract attempt are only counted once
                if isinstance(record, dict) and "message_id" in record and "event" in record:
                    if record["message_id"] in seen_message_ids:
                        continue
                    seen_message_ids.add(record["message_id"])
                    all_events.append(record["event"])
                else:
                    all_events.append(record)

    aggregated = aggregate_events(all_events)
    key = write_staged(PIPELINE, execution_id, "aggregated", aggregated)
    return {
        "staged_key": key,
        "total_events": len(all_events),
        "summary": aggregated["summary"],
    }


def load_to_data_lake(event: dict) -> dict:
    ds = _ds(event)
    aggregated = read_staged(event["transform"]["staged_key"])
    bucket = env("DATA_LAKE_BUCKET")
    prefix = env("ANALYTICS_PREFIX")
    s3 = client("s3")

    partition = f"{prefix}/year={ds[:4]}/month={ds[5:7]}/day={ds[8:10]}"

    s3.put_object(
        Bucket=bucket,
        Key=f"{partition}/summary.json.gz",
        Body=gzip.compress(json.dumps(aggregated["summary"], indent=2).encode("utf-8")),
    )
    s3.put_object(
        Bucket=bucket,
        Key=f"{partition}/hourly_breakdown.json.gz",
        Body=gzip.compress(
            json.dumps(aggregated["hourly_breakdown"], indent=2).encode("utf-8")
        ),
    )

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for user in aggregated["user_summaries"]:
            gz.write(json.dumps(user).encode("utf-8"))
            gz.write(b"\n")
    s3.put_object(Bucket=bucket, Key=f"{partition}/top_users.jsonl.gz", Body=buf.getvalue())

    return {"partition": partition, "objects_written": 3}


def update_postgres_aggregates(event: dict) -> dict:
    ds = _ds(event)
    summary = event["transform"]["summary"]

    with pg_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(UPSERT_SQL, (
                ds,
                summary["active_users"],
                summary["active_documents"],
                summary["active_files"],
                summary["total_events"],
                summary["documents_created"],
                summary["documents_edited"],
                summary["comments_added"],
                summary["files_uploaded"],
                summary["files_shared"],
                summary["files_deleted"],
                summary["bytes_uploaded"],
            ))
        conn.commit()

    return {"report_date": ds, "rows_upserted": 1}


def generate_report(event: dict) -> dict:
    ds = _ds(event)
    aggregated = read_staged(event["transform"]["staged_key"])
    summary = aggregated["summary"]

    report = {
        "report_type": "daily_analytics",
        "report_date": ds,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "summary": summary,
        "highlights": {
            "peak_hour": peak_hour(aggregated["hourly_breakdown"]),
            "most_active_users": [
                u["user_id"] for u in aggregated["user_summaries"][:5]
            ],
        },
        "document_metrics": {
            "created": summary["documents_created"],
            "edited": summary["documents_edited"],
            "comments": summary["comments_added"],
        },
        "file_metrics": {
            "uploaded": summary["files_uploaded"],
            "shared": summary["files_shared"],
            "deleted": summary["files_deleted"],
            "bytes_uploaded": summary["bytes_uploaded"],
        },
    }

    report_key = f"reports/analytics/daily/{ds}/report.json"
    client("s3").put_object(
        Bucket=env("DATA_LAKE_BUCKET"),
        Key=report_key,
        Body=json.dumps(report, indent=2).encode("utf-8"),
    )
    return {"report_key": report_key, "total_events": summary["total_events"]}


handler = make_handler(PIPELINE, {
    "extract_from_sqs": extract_from_sqs,
    "extract_from_dynamodb": extract_from_dynamodb,
    "transform_events": transform_events,
    "load_to_data_lake": load_to_data_lake,
    "update_postgres_aggregates": update_postgres_aggregates,
    "generate_report": generate_report,
})

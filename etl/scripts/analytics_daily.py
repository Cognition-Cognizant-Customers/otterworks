#!/usr/bin/env python3
# analytics_daily.py - Daily analytics aggregation pipeline
# Originally Python 2.7, minimally ported to Python 3 in 2021
# Pulls events from SQS + DynamoDB, aggregates, loads to S3 and PostgreSQL
#
# Owner: Jake (data-team@otterworks.dev) -- Jake left mid-2020
# TODO ETL-078: Refactor this into proper modules (deferred Q4 2019)
# TODO ETL-142: Move credentials to secrets manager (deferred Q3 2020)
# TODO ETL-201: Add unit tests (never prioritized)

import configparser
import gzip
import io
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
import pandas as pd
import psycopg2


# TODO ETL-089: Make queue URL configurable per environment (2019-11-15)
SQS_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/otterworks-analytics"
SQS_MAX_MESSAGES = 10000  # hardcoded limit
SQS_BATCH_SIZE = 10
SQS_MAX_CONSECUTIVE_ERRORS = 3
DYNAMODB_TABLE_NAME = "otterworks-analytics-events"
USER_ID_COLUMNS = ["ownerId", "editedBy", "authorId", "deletedBy", "userId"]

UPSERT_SQL = """
    INSERT INTO analytics_daily_summary (
        report_date, active_users, active_documents, active_files,
        total_events, documents_created, documents_edited,
        comments_added, files_uploaded, files_shared,
        files_deleted, bytes_uploaded, updated_at
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
    )
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


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_config():
    config = configparser.ConfigParser()
    config.read("/opt/etl/config.ini")
    return config


def aws_credentials(config):
    return {
        "aws_access_key_id": config.get("aws", "access_key"),
        "aws_secret_access_key": config.get("aws", "secret_key"),
        "region_name": config.get("aws", "region"),
    }


def receive_sqs_batch(sqs_client):
    return sqs_client.receive_message(
        QueueUrl=SQS_QUEUE_URL,
        MaxNumberOfMessages=SQS_BATCH_SIZE,
        WaitTimeSeconds=5,
        AttributeNames=["All"],
        MessageAttributeNames=["All"],
    )


def parse_sqs_messages(messages):
    """Return (parsed events, delete entries) for the messages that decoded cleanly."""
    events = []
    entries_to_delete = []

    for msg in messages:
        try:
            events.append(json.loads(msg["Body"]))
            entries_to_delete.append(
                {"Id": msg["MessageId"], "ReceiptHandle": msg["ReceiptHandle"]}
            )
        except:
            # TODO ETL-103: Add dead-letter queue for malformed messages (2020-01-08)
            pass

    return events, entries_to_delete


def extract_sqs_events(credentials):
    """Drain the analytics SQS queue, deleting the messages that were consumed."""
    sqs_client = boto3.client("sqs", **credentials)

    all_sqs_events = []
    messages_processed = 0
    consecutive_errors = 0

    print("[%s] Polling SQS queue: %s" % (now_str(), SQS_QUEUE_URL))

    while messages_processed < SQS_MAX_MESSAGES:
        try:
            response = receive_sqs_batch(sqs_client)
            consecutive_errors = 0
        except:
            # TODO ETL-103: Add dead-letter queue for failed SQS calls (2020-01-08)
            consecutive_errors += 1
            print("[%s] WARNING: SQS receive failed (%d consecutive)" % (now_str(), consecutive_errors))
            if consecutive_errors >= SQS_MAX_CONSECUTIVE_ERRORS:
                print("[%s] ERROR: Too many SQS failures, giving up" % now_str())
                break
            continue

        messages = response.get("Messages", [])
        if not messages:
            print("[%s] No more messages after %d processed" % (now_str(), messages_processed))
            break

        events, entries_to_delete = parse_sqs_messages(messages)
        all_sqs_events.extend(events)

        if entries_to_delete:
            sqs_client.delete_message_batch(QueueUrl=SQS_QUEUE_URL, Entries=entries_to_delete)

        messages_processed += len(messages)

    print("[%s] Extracted %d events from SQS" % (now_str(), len(all_sqs_events)))
    return all_sqs_events


def normalize_decimals(item):
    """Convert DynamoDB Decimals to native types for json serialization later."""
    for k, v in item.items():
        if isinstance(v, Decimal):
            item[k] = int(v) if v == int(v) else float(v)
    return item


def extract_dynamo_events(credentials, ds):
    """Scan the analytics events table for the given partition date."""
    dynamodb = boto3.resource("dynamodb", **credentials)
    table = dynamodb.Table(DYNAMODB_TABLE_NAME)

    all_dynamo_events = []
    scan_kwargs = {
        "FilterExpression": "begins_with(event_date, :ds)",
        "ExpressionAttributeValues": {":ds": ds},
    }

    while True:
        response = table.scan(**scan_kwargs)
        all_dynamo_events.extend(
            normalize_decimals(item) for item in response.get("Items", [])
        )

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    print("[%s] Extracted %d events from DynamoDB for %s" % (
        now_str(), len(all_dynamo_events), ds
    ))
    return all_dynamo_events


def parse_hour(ts):
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return "%02d" % dt.hour
    except:
        pass
    return "00"


def build_dataframe(all_events):
    """Build the event frame with normalized event type, user id, and hour columns."""
    # TODO ETL-155: This pandas approach is slow for large datasets, consider PySpark (2020-03-22)
    df = pd.DataFrame(all_events)

    # Normalize event type field name
    if "event_type" in df.columns and "eventType" not in df.columns:
        df["eventType"] = df["event_type"]
    if "eventType" not in df.columns:
        df["eventType"] = "unknown"

    # Resolve user ID from whichever field is populated
    df["resolved_user_id"] = "unknown"
    for col in USER_ID_COLUMNS:
        if col in df.columns:
            mask = (df["resolved_user_id"] == "unknown") & df[col].notna() & (df[col] != "")
            df.loc[mask, "resolved_user_id"] = df.loc[mask, col]

    # Parse timestamps for hourly bucketing
    df["hour"] = "00"
    if "timestamp" in df.columns:
        df["hour"] = df["timestamp"].apply(parse_hour)

    return df


def count_by(df, group_column):
    """Count events per (group value, event type) as a nested dict."""
    counts = {}
    for _, row in df.iterrows():
        group = row.get(group_column, "unknown")
        etype = row.get("eventType", "unknown")
        bucket = counts.setdefault(group, {})
        bucket[etype] = bucket.get(etype, 0) + 1
    return counts


def build_user_summaries(df):
    """Top 100 users by total actions."""
    user_summaries = [
        {"user_id": uid, "actions": actions, "total": sum(actions.values())}
        for uid, actions in count_by(df, "resolved_user_id").items()
    ]
    user_summaries.sort(key=lambda x: x["total"], reverse=True)
    return user_summaries[:100]


def unique_ids(df, id_column):
    if id_column not in df.columns:
        return []
    return df[id_column].dropna().unique()


def compute_document_metrics(df):
    """Return (metrics dict, set of active document ids)."""
    active_documents = set()

    doc_created = df[df["eventType"] == "document_created"]
    active_documents.update(unique_ids(doc_created, "documentId"))

    doc_edited = df[df["eventType"] == "document_edited"]
    active_documents.update(unique_ids(doc_edited, "documentId"))

    metrics = {
        "created": len(doc_created),
        "edited": len(doc_edited),
        "comments": len(df[df["eventType"] == "comment_added"]),
    }
    return metrics, active_documents


def compute_file_metrics(df):
    """Return (metrics dict, set of active file ids)."""
    active_files = set()

    uploaded = df[df["eventType"] == "file_uploaded"]
    active_files.update(unique_ids(uploaded, "fileId"))
    bytes_uploaded = 0
    if "sizeBytes" in df.columns:
        bytes_uploaded = int(uploaded["sizeBytes"].fillna(0).sum())

    shared = df[df["eventType"] == "file_shared"]
    active_files.update(unique_ids(shared, "fileId"))

    deleted = df[df["eventType"] == "file_deleted"]
    active_files.update(unique_ids(deleted, "fileId"))

    metrics = {
        "uploaded": len(uploaded),
        "shared": len(shared),
        "deleted": len(deleted),
        "bytes_uploaded": bytes_uploaded,
    }
    return metrics, active_files


def build_summary(total_events, active_users, active_documents, active_files,
                  document_metrics, file_metrics):
    return {
        "active_users": len(active_users),
        "active_documents": len(active_documents),
        "active_files": len(active_files),
        "total_events": total_events,
        "documents_created": document_metrics["created"],
        "documents_edited": document_metrics["edited"],
        "comments_added": document_metrics["comments"],
        "files_uploaded": file_metrics["uploaded"],
        "files_shared": file_metrics["shared"],
        "files_deleted": file_metrics["deleted"],
        "bytes_uploaded": file_metrics["bytes_uploaded"],
    }


def load_to_data_lake(credentials, bucket, analytics_prefix, ds, summary,
                      hourly_breakdown, user_summaries):
    """Write summary, hourly breakdown, and top users to the S3 data lake."""
    s3_client = boto3.client("s3", **credentials)

    partition_key = "%s/year=%s/month=%s/day=%s" % (analytics_prefix, ds[:4], ds[5:7], ds[8:10])

    # Write summary
    summary_key = "%s/summary.json.gz" % partition_key
    s3_client.put_object(
        Bucket=bucket,
        Key=summary_key,
        Body=gzip.compress(json.dumps(summary, indent=2).encode("utf-8")),
    )
    print("[%s] Uploaded summary to s3://%s/%s" % (now_str(), bucket, summary_key))

    # Write hourly breakdown
    s3_client.put_object(
        Bucket=bucket,
        Key="%s/hourly_breakdown.json.gz" % partition_key,
        Body=gzip.compress(json.dumps(hourly_breakdown, indent=2).encode("utf-8")),
    )

    # Write top users as JSONL
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for user in user_summaries:
            gz.write(json.dumps(user).encode("utf-8"))
            gz.write(b"\n")
    s3_client.put_object(
        Bucket=bucket,
        Key="%s/top_users.jsonl.gz" % partition_key,
        Body=buf.getvalue(),
    )

    print("[%s] Loaded analytics data to s3://%s/%s" % (now_str(), bucket, partition_key))


def upsert_postgres_summary(config, ds, summary):
    """Upsert the daily aggregates; failures are logged but not fatal."""
    db_host = config.get("database", "host")
    db_port = config.getint("database", "port")
    db_name = config.get("database", "database")

    print("[%s] Connecting to PostgreSQL at %s:%d/%s" % (now_str(), db_host, db_port, db_name))

    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            dbname=db_name,
            user=config.get("database", "user"),
            password=config.get("database", "password"),
        )
        cursor = conn.cursor()
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
        print("[%s] Updated PostgreSQL daily aggregates for %s" % (now_str(), ds))
    except Exception as e:
        print("[%s] ERROR: PostgreSQL update failed: %s" % (now_str(), str(e)))
        if conn:
            conn.rollback()
        # don't exit -- still try to generate report
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def find_peak_hour(hourly_breakdown):
    if not hourly_breakdown:
        return None
    peak = max(hourly_breakdown.items(), key=lambda x: sum(x[1].values()))
    return {"hour": peak[0], "event_count": sum(peak[1].values())}


def build_report(ds, summary, hourly_breakdown, user_summaries,
                 document_metrics, file_metrics):
    return {
        "report_type": "daily_analytics",
        "report_date": ds,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "summary": summary,
        "highlights": {
            "peak_hour": find_peak_hour(hourly_breakdown),
            "most_active_users": [u["user_id"] for u in user_summaries[:5]],
        },
        "document_metrics": document_metrics,
        "file_metrics": file_metrics,
    }


def main():
    print("[%s] analytics_daily.py starting..." % now_str())

    config = load_config()
    credentials = aws_credentials(config)
    data_lake_bucket = config.get("s3", "data_lake_bucket")
    analytics_prefix = config.get("s3", "analytics_prefix")

    # today's date for partitioning
    ds = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    print("[%s] Processing analytics for date: %s" % (now_str(), ds))

    all_events = extract_sqs_events(credentials) + extract_dynamo_events(credentials, ds)
    print("[%s] Total events to process: %d" % (now_str(), len(all_events)))

    if len(all_events) == 0:
        print("[%s] WARNING: No events found, exiting" % now_str())
        sys.exit(0)

    df = build_dataframe(all_events)

    active_users = set(df["resolved_user_id"].unique()) - {"unknown"}
    user_summaries = build_user_summaries(df)
    document_metrics, active_documents = compute_document_metrics(df)
    file_metrics, active_files = compute_file_metrics(df)
    hourly_breakdown = dict(sorted(count_by(df, "hour").items()))

    summary = build_summary(
        len(all_events), active_users, active_documents, active_files,
        document_metrics, file_metrics,
    )

    print("[%s] Aggregation complete: %d events, %d active users, %d documents, %d files" % (
        now_str(),
        len(all_events),
        len(active_users),
        len(active_documents),
        len(active_files),
    ))

    load_to_data_lake(
        credentials, data_lake_bucket, analytics_prefix, ds,
        summary, hourly_breakdown, user_summaries,
    )

    upsert_postgres_summary(config, ds, summary)

    report = build_report(
        ds, summary, hourly_breakdown, user_summaries, document_metrics, file_metrics
    )

    report_key = "reports/analytics/daily/%s/report.json" % ds
    s3_client_report = boto3.client("s3", **credentials)
    s3_client_report.put_object(
        Bucket=data_lake_bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2).encode("utf-8"),
    )

    print("[%s] Generated daily analytics report: %d events, %d active users" % (
        now_str(),
        summary["total_events"],
        summary["active_users"],
    ))
    print("[%s] analytics_daily.py completed successfully" % now_str())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[%s] FATAL: %s" % (now_str(), str(e)))
        sys.exit(1)

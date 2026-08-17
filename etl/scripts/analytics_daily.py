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
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import pandas as pd
import psycopg2

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

USER_ID_COLUMNS = ["ownerId", "editedBy", "authorId", "deletedBy", "userId"]


def log(message):
    print("[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message))


def receive_sqs_batch(sqs_client, queue_url, batch_size):
    """Receive one batch of messages, or None when the call fails."""
    try:
        response = sqs_client.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=batch_size,
            WaitTimeSeconds=5,
            AttributeNames=["All"],
            MessageAttributeNames=["All"],
        )
    except Exception:
        # TODO ETL-103: Add dead-letter queue for failed SQS calls (2020-01-08)
        return None
    return response.get("Messages", [])


def parse_sqs_messages(messages, events):
    """Append parsed message bodies to events, returning deletable entries."""
    entries_to_delete = []
    for msg in messages:
        try:
            events.append(json.loads(msg["Body"]))
        except Exception:
            # TODO ETL-103: Add dead-letter queue for malformed messages (2020-01-08)
            continue
        entries_to_delete.append(
            {"Id": msg["MessageId"], "ReceiptHandle": msg["ReceiptHandle"]}
        )
    return entries_to_delete


def extract_sqs_events(sqs_client, queue_url, max_messages, batch_size):
    """Drain the analytics queue up to max_messages, deleting what we read."""
    events = []
    messages_processed = 0
    consecutive_errors = 0

    while messages_processed < max_messages:
        messages = receive_sqs_batch(sqs_client, queue_url, batch_size)

        if messages is None:
            consecutive_errors += 1
            log("WARNING: SQS receive failed (%d consecutive)" % consecutive_errors)
            if consecutive_errors >= 3:
                log("ERROR: Too many SQS failures, giving up")
                return events
            continue

        consecutive_errors = 0
        if not messages:
            log("No more messages after %d processed" % messages_processed)
            return events

        entries_to_delete = parse_sqs_messages(messages, events)
        if entries_to_delete:
            sqs_client.delete_message_batch(
                QueueUrl=queue_url, Entries=entries_to_delete
            )

        messages_processed += len(messages)

    return events


def normalize_decimals(item):
    """Convert DynamoDB Decimals to native types for later json serialization."""
    for k, v in item.items():
        if isinstance(v, Decimal):
            item[k] = int(v) if v == int(v) else float(v)
    return item


def extract_dynamo_events(table, ds):
    """Scan the analytics events table for a single partition date."""
    events = []
    scan_kwargs = {
        "FilterExpression": "begins_with(event_date, :ds)",
        "ExpressionAttributeValues": {":ds": ds},
    }

    while True:
        response = table.scan(**scan_kwargs)
        events.extend(normalize_decimals(item) for item in response.get("Items", []))

        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return events
        scan_kwargs["ExclusiveStartKey"] = last_key


def parse_hour(ts):
    """Bucket an ISO timestamp into a two-digit hour, defaulting to '00'."""
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return "%02d" % dt.hour
    except ValueError:
        pass
    return "00"


def build_dataframe(events):
    """Build the event frame with normalized event type, user id and hour."""
    df = pd.DataFrame(events)

    if "event_type" in df.columns and "eventType" not in df.columns:
        df["eventType"] = df["event_type"]
    if "eventType" not in df.columns:
        df["eventType"] = "unknown"

    df["resolved_user_id"] = "unknown"
    for col in USER_ID_COLUMNS:
        if col in df.columns:
            mask = (df["resolved_user_id"] == "unknown") & df[col].notna() & (df[col] != "")
            df.loc[mask, "resolved_user_id"] = df.loc[mask, col]

    df["hour"] = "00"
    if "timestamp" in df.columns:
        df["hour"] = df["timestamp"].apply(parse_hour)

    return df


def count_by(df, group_column):
    """Count events per (group value, event type)."""
    counts = {}
    for _, row in df.iterrows():
        group = row.get(group_column, "unknown")
        etype = row.get("eventType", "unknown")
        by_type = counts.setdefault(group, {})
        by_type[etype] = by_type.get(etype, 0) + 1
    return counts


def top_user_summaries(user_action_counts, limit=100):
    """Rank users by total actions, keeping the busiest `limit` of them."""
    summaries = [
        {"user_id": uid, "actions": actions, "total": sum(actions.values())}
        for uid, actions in user_action_counts.items()
    ]
    summaries.sort(key=lambda x: x["total"], reverse=True)
    return summaries[:limit]


def events_of_type(df, event_type):
    """Return the rows for one event type (empty frame when untyped)."""
    if "eventType" not in df.columns:
        return df.iloc[0:0]
    return df[df["eventType"] == event_type]


def collect_ids(df, column, active_ids):
    """Add the non-null ids of a column to the active set."""
    if column in df.columns:
        active_ids.update(df[column].dropna().unique())


def document_metrics(df, active_documents):
    """Count document activity and record the documents it touched."""
    created = events_of_type(df, "document_created")
    collect_ids(created, "documentId", active_documents)

    edited = events_of_type(df, "document_edited")
    collect_ids(edited, "documentId", active_documents)

    return {
        "created": len(created),
        "edited": len(edited),
        "comments": len(events_of_type(df, "comment_added")),
    }


def file_metrics(df, active_files):
    """Count file activity and record the files it touched."""
    uploaded = events_of_type(df, "file_uploaded")
    collect_ids(uploaded, "fileId", active_files)

    shared = events_of_type(df, "file_shared")
    collect_ids(shared, "fileId", active_files)

    deleted = events_of_type(df, "file_deleted")
    collect_ids(deleted, "fileId", active_files)

    bytes_uploaded = 0
    if "sizeBytes" in uploaded.columns:
        bytes_uploaded = int(uploaded["sizeBytes"].fillna(0).sum())

    return {
        "uploaded": len(uploaded),
        "shared": len(shared),
        "deleted": len(deleted),
        "bytes_uploaded": bytes_uploaded,
    }


def build_summary(total_events, active_users, active_documents, active_files, docs, files):
    return {
        "active_users": len(active_users),
        "active_documents": len(active_documents),
        "active_files": len(active_files),
        "total_events": total_events,
        "documents_created": docs["created"],
        "documents_edited": docs["edited"],
        "comments_added": docs["comments"],
        "files_uploaded": files["uploaded"],
        "files_shared": files["shared"],
        "files_deleted": files["deleted"],
        "bytes_uploaded": files["bytes_uploaded"],
    }


def gzip_json(payload):
    return gzip.compress(json.dumps(payload, indent=2).encode("utf-8"))


def gzip_jsonl(records):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for record in records:
            gz.write(json.dumps(record).encode("utf-8"))
            gz.write(b"\n")
    return buf.getvalue()


def load_to_data_lake(s3_client, bucket, partition_key, summary, hourly_breakdown, user_summaries):
    """Write the summary, hourly breakdown and top users to the data lake."""
    summary_key = "%s/summary.json.gz" % partition_key
    s3_client.put_object(Bucket=bucket, Key=summary_key, Body=gzip_json(summary))
    log("Uploaded summary to s3://%s/%s" % (bucket, summary_key))

    s3_client.put_object(
        Bucket=bucket,
        Key="%s/hourly_breakdown.json.gz" % partition_key,
        Body=gzip_json(hourly_breakdown),
    )

    s3_client.put_object(
        Bucket=bucket,
        Key="%s/top_users.jsonl.gz" % partition_key,
        Body=gzip_jsonl(user_summaries),
    )

    log("Loaded analytics data to s3://%s/%s" % (bucket, partition_key))


def upsert_daily_summary(db_params, ds, summary):
    """Upsert the daily aggregates; failures are logged, not fatal."""
    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(**db_params)
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
        log("Updated PostgreSQL daily aggregates for %s" % ds)
    except Exception as e:
        log("ERROR: PostgreSQL update failed: %s" % str(e))
        if conn:
            conn.rollback()
        # don't exit -- still try to generate report
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def peak_hour_of(hourly_breakdown):
    """Return the busiest hour bucket, or None when there are no events."""
    if not hourly_breakdown:
        return None
    hour, events = max(hourly_breakdown.items(), key=lambda x: sum(x[1].values()))
    return {"hour": hour, "event_count": sum(events.values())}


def main():
    log("analytics_daily.py starting...")

    # ---- Load config ----
    config = configparser.ConfigParser()
    config.read("/opt/etl/config.ini")

    aws_access_key = config.get("aws", "access_key")
    aws_secret_key = config.get("aws", "secret_key")
    aws_region = config.get("aws", "region")

    db_params = {
        "host": config.get("database", "host"),
        "port": config.getint("database", "port"),
        "dbname": config.get("database", "database"),
        "user": config.get("database", "user"),
        "password": config.get("database", "password"),
    }

    data_lake_bucket = config.get("s3", "data_lake_bucket")
    analytics_prefix = config.get("s3", "analytics_prefix")

    # today's date for partitioning
    ds = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    log("Processing analytics for date: %s" % ds)

    # ---- Extract from SQS ----
    # TODO ETL-089: Make queue URL configurable per environment (2019-11-15)
    sqs_queue_url = "https://sqs.us-east-1.amazonaws.com/123456789012/otterworks-analytics"
    sqs_client = boto3.client(
        "sqs",
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region,
    )

    log("Polling SQS queue: %s" % sqs_queue_url)

    all_sqs_events = extract_sqs_events(
        sqs_client, sqs_queue_url, max_messages=10000, batch_size=10
    )

    log("Extracted %d events from SQS" % len(all_sqs_events))

    # ---- Extract from DynamoDB ----
    dynamodb = boto3.resource(
        "dynamodb",
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region,
    )
    table = dynamodb.Table("otterworks-analytics-events")

    all_dynamo_events = extract_dynamo_events(table, ds)

    log("Extracted %d events from DynamoDB for %s" % (len(all_dynamo_events), ds))

    # ---- Combine all events ----
    all_events = all_sqs_events + all_dynamo_events
    log("Total events to process: %d" % len(all_events))

    if len(all_events) == 0:
        log("WARNING: No events found, exiting")
        sys.exit(0)

    # ---- Transform and aggregate using pandas ----
    # TODO ETL-155: This pandas approach is slow for large datasets, consider PySpark (2020-03-22)
    df = build_dataframe(all_events)

    active_users = set(df["resolved_user_id"].unique()) - {"unknown"}
    user_summaries = top_user_summaries(count_by(df, "resolved_user_id"))
    hourly_breakdown = dict(sorted(count_by(df, "hour").items()))

    active_documents = set()
    active_files = set()
    docs = document_metrics(df, active_documents)
    files = file_metrics(df, active_files)

    summary = build_summary(
        len(all_events), active_users, active_documents, active_files, docs, files
    )

    log("Aggregation complete: %d events, %d active users, %d documents, %d files" % (
        len(all_events),
        len(active_users),
        len(active_documents),
        len(active_files),
    ))

    # ---- Load to S3 data lake ----
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region,
    )

    partition_key = "%s/year=%s/month=%s/day=%s" % (analytics_prefix, ds[:4], ds[5:7], ds[8:10])
    load_to_data_lake(
        s3_client,
        data_lake_bucket,
        partition_key,
        summary,
        hourly_breakdown,
        user_summaries,
    )

    # ---- Upsert PostgreSQL aggregates ----
    log("Connecting to PostgreSQL at %s:%d/%s" % (
        db_params["host"], db_params["port"], db_params["dbname"]
    ))
    upsert_daily_summary(db_params, ds, summary)

    # ---- Generate daily report ----
    report = {
        "report_type": "daily_analytics",
        "report_date": ds,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "summary": summary,
        "highlights": {
            "peak_hour": peak_hour_of(hourly_breakdown),
            "most_active_users": [u["user_id"] for u in user_summaries[:5]],
        },
        "document_metrics": docs,
        "file_metrics": files,
    }

    report_key = "reports/analytics/daily/%s/report.json" % ds
    s3_client_report = boto3.client(
        "s3",
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region,
    )
    s3_client_report.put_object(
        Bucket=data_lake_bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2).encode("utf-8"),
    )

    log("Generated daily analytics report: %d events, %d active users" % (
        summary["total_events"],
        summary["active_users"],
    ))
    log("analytics_daily.py completed successfully")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[%s] FATAL: %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(e)))
        sys.exit(1)

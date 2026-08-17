#!/usr/bin/env python3
# user_activity_daily.py - Daily user activity report generation
# Originally Python 2.7, minimally ported to Python 3 in 2021
# Queries PostgreSQL aggregates, reads per-user S3 data, generates
# activity reports, stores to S3 for admin-service consumption
#
# Owner: Jake (data-team@otterworks.dev) -- Jake left mid-2020
# TODO ETL-098: Optimize S3 reads with range requests (2019-12-01)
# TODO ETL-160: Cache PostgreSQL connection across runs (deferred Q2 2020)
# TODO ETL-210: Add email notification for report generation (never done)

import configparser
import gzip
import json
import sys
from datetime import datetime, timedelta, timezone

import boto3
import psycopg2


SUMMARY_COLUMNS = [
    "report_date",
    "active_users",
    "active_documents",
    "active_files",
    "total_events",
    "documents_created",
    "documents_edited",
    "comments_added",
    "files_uploaded",
    "files_shared",
    "files_deleted",
    "bytes_uploaded",
]

SUMMARY_SQL = """
    SELECT
        report_date,
        active_users,
        active_documents,
        active_files,
        total_events,
        documents_created,
        documents_edited,
        comments_added,
        files_uploaded,
        files_shared,
        files_deleted,
        bytes_uploaded
    FROM analytics_daily_summary
    WHERE report_date BETWEEN %s::date - interval '%s days' AND %s::date
    ORDER BY report_date;
"""


def row_to_summary(row):
    """Map a result row onto the summary columns, normalizing dates."""
    record = {}
    for i, col in enumerate(SUMMARY_COLUMNS):
        val = row[i]
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        record[col] = val
    return record


def fetch_daily_summaries(db_params, ds, lookback_days):
    """Read the analytics daily aggregates for the lookback window."""
    conn = None
    cursor = None
    try:
        conn = psycopg2.connect(**db_params)
        cursor = conn.cursor()
        cursor.execute(SUMMARY_SQL, (ds, lookback_days, ds))
        return [row_to_summary(row) for row in cursor.fetchall()]
    except Exception as e:
        print("[%s] ERROR: PostgreSQL query failed: %s" % (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(e)
        ))
        sys.exit(1)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def accumulate_user_day(user_totals, user_data):
    """Fold one day's per-user record into the running totals."""
    uid = user_data.get("user_id", "unknown")
    totals = user_totals.setdefault(uid, {
        "user_id": uid,
        "total_actions": 0,
        "active_days": 0,
        "actions_by_type": {},
    })

    totals["total_actions"] += user_data.get("total", 0)
    totals["active_days"] += 1

    for action_type, count in user_data.get("actions", {}).items():
        prev = totals["actions_by_type"].get(action_type, 0)
        totals["actions_by_type"][action_type] = prev + count


def read_day_user_totals(s3_client, bucket, key, user_totals):
    """Fold one day's top_users file (if present) into the running totals."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        decompressed = gzip.decompress(response["Body"].read()).decode("utf-8")
    except Exception:
        # S3 key might not exist for every day -- silently skip
        # TODO ETL-098: Log missing days for debugging
        return

    for line in decompressed.strip().split("\n"):
        if line:
            accumulate_user_day(user_totals, json.loads(line))


def aggregate_user_activity(s3_client, bucket, ds, lookback_days):
    """Aggregate per-user activity across the lookback window."""
    user_totals = {}
    execution_date = datetime.strptime(ds, "%Y-%m-%d")

    for day_offset in range(lookback_days):
        check_date = execution_date - timedelta(days=day_offset)
        key = "analytics/daily/year=%s/month=%s/day=%s/top_users.jsonl.gz" % (
            check_date.strftime("%Y"),
            check_date.strftime("%m"),
            check_date.strftime("%d"),
        )
        read_day_user_totals(s3_client, bucket, key, user_totals)

    return sorted(
        user_totals.values(), key=lambda x: x["total_actions"], reverse=True
    )


def main():
    print("[%s] user_activity_daily.py starting..." % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # ---- Load config ----
    config = configparser.ConfigParser()
    config.read("/opt/etl/config.ini")

    aws_access_key = config.get("aws", "access_key")
    aws_secret_key = config.get("aws", "secret_key")
    aws_region = config.get("aws", "region")

    db_host = config.get("database", "host")
    db_port = config.getint("database", "port")
    db_name = config.get("database", "database")
    db_user = config.get("database", "user")
    db_password = config.get("database", "password")

    data_lake_bucket = config.get("s3", "data_lake_bucket")

    ds = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    lookback_days = 30
    s3_reports_prefix = "reports/user-activity"

    # ---- Query PostgreSQL for analytics aggregates ----
    print("[%s] Querying PostgreSQL for analytics aggregates (lookback: %d days)..." % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), lookback_days
    ))

    daily_summaries = fetch_daily_summaries(
        {
            "host": db_host,
            "port": db_port,
            "dbname": db_name,
            "user": db_user,
            "password": db_password,
        },
        ds,
        lookback_days,
    )

    print("[%s] Retrieved %d daily summary records" % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(daily_summaries)
    ))

    # ---- Read per-user activity data from S3 ----
    print("[%s] Reading per-user activity data from S3 (lookback: %d days)..." % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), lookback_days
    ))

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region,
    )

    user_list = aggregate_user_activity(
        s3_client, data_lake_bucket, ds, lookback_days
    )
    print("[%s] Aggregated activity for %d users over %d days" % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(user_list), lookback_days
    ))

    # ---- Generate user activity report ----
    total_events = sum(d.get("total_events", 0) for d in daily_summaries)
    total_users = max((d.get("active_users", 0) for d in daily_summaries), default=0)
    avg_daily_events = total_events / len(daily_summaries) if daily_summaries else 0

    report = {
        "report_type": "user_activity",
        "report_date": ds,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "lookback_days": lookback_days,
        "trends": {
            "total_events": total_events,
            "peak_active_users": total_users,
            "avg_daily_events": round(avg_daily_events, 2),
            "reporting_days": len(daily_summaries),
        },
        "daily_summaries": daily_summaries,
        "user_summaries": user_list[:500],
        "top_users": user_list[:20],
    }

    # ---- Store reports to S3 ----
    print("[%s] Storing reports to S3..." % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    s3_client_upload = boto3.client(
        "s3",
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name=aws_region,
    )

    # Store full report
    report_key = "%s/%s/activity_report.json" % (s3_reports_prefix, ds)
    s3_client_upload.put_object(
        Bucket=data_lake_bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2, default=str).encode("utf-8"),
    )

    # Store latest pointer for admin-service
    latest_key = "%s/latest/activity_report.json" % s3_reports_prefix
    s3_client_upload.put_object(
        Bucket=data_lake_bucket,
        Key=latest_key,
        Body=json.dumps(report, indent=2, default=str).encode("utf-8"),
    )

    # Store per-user summaries as JSONL for individual user lookups
    user_summaries = report.get("user_summaries", [])
    if user_summaries:
        users_key = "%s/%s/user_summaries.jsonl" % (s3_reports_prefix, ds)
        lines = [json.dumps(u, default=str) for u in user_summaries]
        s3_client_upload.put_object(
            Bucket=data_lake_bucket,
            Key=users_key,
            Body=("\n".join(lines) + "\n").encode("utf-8"),
        )

    print("[%s] Stored activity report: %d user summaries at s3://%s/%s" % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        len(user_summaries),
        data_lake_bucket,
        report_key,
    ))
    print("[%s] user_activity_daily.py completed successfully" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[%s] FATAL: %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(e)))
        sys.exit(1)

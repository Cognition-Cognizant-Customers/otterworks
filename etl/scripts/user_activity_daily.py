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


LOOKBACK_DAYS = 30
S3_REPORTS_PREFIX = "reports/user-activity"
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


def row_to_record(row):
    record = {}
    for i, col in enumerate(SUMMARY_COLUMNS):
        val = row[i]
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        record[col] = val
    return record


def query_daily_summaries(config, ds):
    """Fetch the daily analytics aggregates for the lookback window."""
    print("[%s] Querying PostgreSQL for analytics aggregates (lookback: %d days)..." % (
        now_str(), LOOKBACK_DAYS
    ))

    conn = None
    cursor = None

    try:
        conn = psycopg2.connect(
            host=config.get("database", "host"),
            port=config.getint("database", "port"),
            dbname=config.get("database", "database"),
            user=config.get("database", "user"),
            password=config.get("database", "password"),
        )
        cursor = conn.cursor()
        cursor.execute(SUMMARY_SQL, (ds, LOOKBACK_DAYS, ds))
        daily_summaries = [row_to_record(row) for row in cursor.fetchall()]

        print("[%s] Retrieved %d daily summary records" % (now_str(), len(daily_summaries)))
        return daily_summaries
    except Exception as e:
        print("[%s] ERROR: PostgreSQL query failed: %s" % (now_str(), str(e)))
        sys.exit(1)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def merge_user_record(user_totals, user_data):
    """Fold one per-day user record into the running per-user totals."""
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


def read_day_user_records(s3_client, bucket, check_date):
    """Read one day's top_users JSONL from S3; empty list when the day is missing."""
    key = "analytics/daily/year=%s/month=%s/day=%s/top_users.jsonl.gz" % (
        check_date.strftime("%Y"), check_date.strftime("%m"), check_date.strftime("%d")
    )

    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        decompressed = gzip.decompress(response["Body"].read()).decode("utf-8")
        return [json.loads(line) for line in decompressed.strip().split("\n") if line]
    except:
        # S3 key might not exist for every day -- silently skip
        # TODO ETL-098: Log missing days for debugging
        return []


def aggregate_user_activity(s3_client, bucket, ds):
    """Aggregate per-user activity over the lookback window, most active first."""
    print("[%s] Reading per-user activity data from S3 (lookback: %d days)..." % (
        now_str(), LOOKBACK_DAYS
    ))

    user_totals = {}
    execution_date = datetime.strptime(ds, "%Y-%m-%d")

    for day_offset in range(LOOKBACK_DAYS):
        check_date = execution_date - timedelta(days=day_offset)
        for user_data in read_day_user_records(s3_client, bucket, check_date):
            merge_user_record(user_totals, user_data)

    user_list = sorted(user_totals.values(), key=lambda x: x["total_actions"], reverse=True)
    print("[%s] Aggregated activity for %d users over %d days" % (
        now_str(), len(user_list), LOOKBACK_DAYS
    ))
    return user_list


def build_report(ds, daily_summaries, user_list):
    total_events = sum(d.get("total_events", 0) for d in daily_summaries)
    total_users = max((d.get("active_users", 0) for d in daily_summaries), default=0)
    avg_daily_events = total_events / len(daily_summaries) if daily_summaries else 0

    return {
        "report_type": "user_activity",
        "report_date": ds,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
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


def store_reports(credentials, bucket, ds, report):
    """Upload the report, the latest pointer, and per-user JSONL. Returns report key."""
    print("[%s] Storing reports to S3..." % now_str())

    s3_client_upload = boto3.client("s3", **credentials)
    report_body = json.dumps(report, indent=2, default=str).encode("utf-8")

    # Store full report
    report_key = "%s/%s/activity_report.json" % (S3_REPORTS_PREFIX, ds)
    s3_client_upload.put_object(Bucket=bucket, Key=report_key, Body=report_body)

    # Store latest pointer for admin-service
    latest_key = "%s/latest/activity_report.json" % S3_REPORTS_PREFIX
    s3_client_upload.put_object(Bucket=bucket, Key=latest_key, Body=report_body)

    # Store per-user summaries as JSONL for individual user lookups
    user_summaries = report.get("user_summaries", [])
    if user_summaries:
        users_key = "%s/%s/user_summaries.jsonl" % (S3_REPORTS_PREFIX, ds)
        lines = [json.dumps(u, default=str) for u in user_summaries]
        s3_client_upload.put_object(
            Bucket=bucket,
            Key=users_key,
            Body=("\n".join(lines) + "\n").encode("utf-8"),
        )

    return report_key


def main():
    print("[%s] user_activity_daily.py starting..." % now_str())

    config = load_config()
    credentials = aws_credentials(config)
    data_lake_bucket = config.get("s3", "data_lake_bucket")

    ds = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    daily_summaries = query_daily_summaries(config, ds)

    s3_client = boto3.client("s3", **credentials)
    user_list = aggregate_user_activity(s3_client, data_lake_bucket, ds)

    report = build_report(ds, daily_summaries, user_list)
    report_key = store_reports(credentials, data_lake_bucket, ds, report)

    print("[%s] Stored activity report: %d user summaries at s3://%s/%s" % (
        now_str(),
        len(report.get("user_summaries", [])),
        data_lake_bucket,
        report_key,
    ))
    print("[%s] user_activity_daily.py completed successfully" % now_str())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[%s] FATAL: %s" % (now_str(), str(e)))
        sys.exit(1)

"""Lambda tasks for the otterworks-user-activity-report state machine.

State machine flow:
  query_analytics_aggregates + query_per_user_activity (parallel)
    -> generate_user_reports -> store_reports_to_s3
"""

import gzip
import json
from datetime import UTC, datetime, timedelta

from botocore.exceptions import ClientError

from otterworks_etl.common.config import client, env
from otterworks_etl.common.db import pg_connection
from otterworks_etl.common.dispatch import make_handler
from otterworks_etl.common.logging import get_logger
from otterworks_etl.common.staging import read_staged, write_staged
from otterworks_etl.user_activity.transform import build_trends, merge_user_day

logger = get_logger(__name__)

PIPELINE = "user-activity"
LOOKBACK_DAYS = 30
REPORTS_PREFIX = "reports/user-activity"

SUMMARY_COLUMNS = [
    "report_date", "active_users", "active_documents", "active_files",
    "total_events", "documents_created", "documents_edited", "comments_added",
    "files_uploaded", "files_shared", "files_deleted", "bytes_uploaded",
]

SUMMARY_SQL = f"""
    SELECT {", ".join(SUMMARY_COLUMNS)}
    FROM analytics_daily_summary
    WHERE report_date BETWEEN %s::date - interval '%s days' AND %s::date
    ORDER BY report_date;
"""


def _ds(event: dict) -> str:
    return (event.get("ds") or datetime.now(tz=UTC).isoformat())[:10]


def query_analytics_aggregates(event: dict) -> dict:
    ds = _ds(event)

    daily_summaries = []
    with pg_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(SUMMARY_SQL, (ds, LOOKBACK_DAYS, ds))
            for row in cursor.fetchall():
                record = {}
                for i, col in enumerate(SUMMARY_COLUMNS):
                    val = row[i]
                    record[col] = val.isoformat() if hasattr(val, "isoformat") else val
                daily_summaries.append(record)

    key = write_staged(PIPELINE, event["execution_id"], "daily_summaries", daily_summaries)
    return {"staged_key": key, "record_count": len(daily_summaries)}


def query_per_user_activity(event: dict) -> dict:
    ds = _ds(event)
    bucket = env("DATA_LAKE_BUCKET")
    s3 = client("s3")
    execution_date = datetime.strptime(ds, "%Y-%m-%d")

    user_totals: dict[str, dict] = {}
    missing_days = []

    for day_offset in range(LOOKBACK_DAYS):
        check_date = execution_date - timedelta(days=day_offset)
        key = check_date.strftime("analytics/daily/year=%Y/month=%m/day=%d/top_users.jsonl.gz")
        try:
            response = s3.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            # without s3:ListBucket a missing key surfaces as AccessDenied
            # instead of NoSuchKey; treat both as a day with no data
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "AccessDenied"):
                missing_days.append(check_date.strftime("%Y-%m-%d"))
                continue
            raise

        decompressed = gzip.decompress(response["Body"].read()).decode("utf-8")
        for line in decompressed.strip().split("\n"):
            if line:
                merge_user_day(user_totals, json.loads(line))

    if missing_days:
        logger.warning("missing daily activity data", extra={"context": {
            "missing_days": missing_days}})

    user_list = sorted(
        user_totals.values(), key=lambda u: u["total_actions"], reverse=True
    )
    key = write_staged(PIPELINE, event["execution_id"], "user_totals", user_list)
    return {
        "staged_key": key,
        "user_count": len(user_list),
        "missing_days": len(missing_days),
    }


def generate_user_reports(event: dict) -> dict:
    ds = _ds(event)
    aggregates, activity = event["queries"]
    daily_summaries = read_staged(aggregates["staged_key"])
    user_list = read_staged(activity["staged_key"])

    report = {
        "report_type": "user_activity",
        "report_date": ds,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "trends": build_trends(daily_summaries),
        "daily_summaries": daily_summaries,
        "user_summaries": user_list[:500],
        "top_users": user_list[:20],
    }

    key = write_staged(PIPELINE, event["execution_id"], "report", report)
    return {"staged_key": key, "user_count": len(user_list)}


def store_reports_to_s3(event: dict) -> dict:
    ds = _ds(event)
    report = read_staged(event["report"]["staged_key"])
    bucket = env("DATA_LAKE_BUCKET")
    s3 = client("s3")

    body = json.dumps(report, indent=2, default=str).encode("utf-8")
    report_key = f"{REPORTS_PREFIX}/{ds}/activity_report.json"
    s3.put_object(Bucket=bucket, Key=report_key, Body=body)
    s3.put_object(
        Bucket=bucket, Key=f"{REPORTS_PREFIX}/latest/activity_report.json", Body=body
    )

    user_summaries = report.get("user_summaries", [])
    if user_summaries:
        lines = [json.dumps(u, default=str) for u in user_summaries]
        s3.put_object(
            Bucket=bucket,
            Key=f"{REPORTS_PREFIX}/{ds}/user_summaries.jsonl",
            Body=("\n".join(lines) + "\n").encode("utf-8"),
        )

    return {"report_key": report_key, "user_summaries": len(user_summaries)}


handler = make_handler(PIPELINE, {
    "query_analytics_aggregates": query_analytics_aggregates,
    "query_per_user_activity": query_per_user_activity,
    "generate_user_reports": generate_user_reports,
    "store_reports_to_s3": store_reports_to_s3,
})

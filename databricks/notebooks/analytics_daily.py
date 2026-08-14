# Databricks notebook source
# Analytics daily — replaces etl/scripts/analytics_daily.py (cron 0 2 * * *).
#
# The legacy script drained SQS, scanned DynamoDB events, normalized event
# types, resolved user ids, and wrote daily aggregates to Postgres + gzip JSON
# to S3. Here the event stream lands as the hourly gzip JSON objects the estate
# already emits (events/<ns>/YYYY/MM/DD/HH.json.gz), and the aggregates become
# governed gold tables.

# COMMAND ----------
import re

from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType

dbutils.widgets.text("catalog", "ow_tp")
dbutils.widgets.text("ns", "dev")

catalog = dbutils.widgets.get("catalog")
ns = dbutils.widgets.get("ns").lower()
if not re.fullmatch(r"[a-z0-9_]{1,64}", catalog):
    raise ValueError(f"invalid catalog: {catalog!r}")
if not re.fullmatch(r"[a-z0-9_]{1,32}", ns):
    raise ValueError(f"invalid namespace: {ns!r}")

landing = f"/Volumes/{catalog}/bronze/landing/{ns}/events"

# COMMAND ----------
# Stage inputs uploaded to the workspace landing area (the demo PAT has
# workspace scope but not the files scope) into the UC landing volume.
import os
import shutil

ws_landing = f"/Workspace/Shared/{catalog}/landing/{ns}/events"
if os.path.isdir(ws_landing):
    # Mirror the staging area exactly: drop stale files from earlier runs.
    shutil.rmtree(landing, ignore_errors=True)
    shutil.copytree(ws_landing, landing, dirs_exist_ok=True)

# COMMAND ----------
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.bronze.events (
        ns STRING,
        event_id STRING,
        event_type STRING,
        user_id STRING,
        resource_id STRING,
        occurred_at TIMESTAMP
    )
    """
)

schema = StructType(
    [
        StructField("event_id", StringType()),
        StructField("event_type", StringType()),
        StructField("user_id", StringType()),
        StructField("resource_id", StringType()),
        StructField("occurred_at", StringType()),
    ]
)

events = (
    spark.read.schema(schema)
    .json(f"{landing}/*/*/*/*.json.gz")
    .select(
        F.lit(ns).alias("ns"),
        "event_id",
        "event_type",
        # Legacy resolved missing (null or empty) user ids to "unknown".
        F.when(F.coalesce(F.col("user_id"), F.lit("")) == "", F.lit("unknown"))
        .otherwise(F.col("user_id"))
        .alias("user_id"),
        "resource_id",
        F.to_timestamp("occurred_at").alias("occurred_at"),
    )
)

spark.sql(f"DELETE FROM `{catalog}`.bronze.events WHERE ns = '{ns}'")
events.write.mode("append").saveAsTable(f"`{catalog}`.bronze.events")
events = spark.table(f"`{catalog}`.bronze.events").where(F.col("ns") == ns)

# COMMAND ----------
for ddl in (
    f"""CREATE TABLE IF NOT EXISTS `{catalog}`.gold.analytics_daily_summary (
        ns STRING, event_date DATE, total_events BIGINT, unique_users BIGINT)""",
    f"""CREATE TABLE IF NOT EXISTS `{catalog}`.gold.analytics_event_type_daily (
        ns STRING, event_date DATE, event_type STRING, event_count BIGINT)""",
    f"""CREATE TABLE IF NOT EXISTS `{catalog}`.gold.analytics_user_actions_daily (
        ns STRING, event_date DATE, user_id STRING, action_count BIGINT)""",
):
    spark.sql(ddl)

by_day = events.withColumn("event_date", F.to_date("occurred_at"))

daily = by_day.groupBy("event_date").agg(
    F.count("*").alias("total_events"),
    # Legacy excludes the "unknown" placeholder from the distinct-user count.
    F.countDistinct(F.when(F.col("user_id") != "unknown", F.col("user_id"))).alias(
        "unique_users"
    ),
)
by_type = by_day.groupBy("event_date", "event_type").agg(F.count("*").alias("event_count"))
by_user = by_day.groupBy("event_date", "user_id").agg(F.count("*").alias("action_count"))

for table, df in (
    ("analytics_daily_summary", daily.select(F.lit(ns).alias("ns"), "*")),
    ("analytics_event_type_daily", by_type.select(F.lit(ns).alias("ns"), "*")),
    ("analytics_user_actions_daily", by_user.select(F.lit(ns).alias("ns"), "*")),
):
    spark.sql(f"DELETE FROM `{catalog}`.gold.{table} WHERE ns = '{ns}'")
    df.write.mode("append").saveAsTable(f"`{catalog}`.gold.{table}")

for row in daily.orderBy("event_date").collect():
    print(f"{row['event_date']}: {row['total_events']} events, {row['unique_users']} users")

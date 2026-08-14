# Databricks notebook source
# User activity daily — replaces etl/scripts/user_activity_daily.py (cron 0 6 * * *).
#
# The legacy script joined 30 days of Postgres daily summaries with the gzip
# top-user files analytics_daily wrote to S3, then emitted full/latest/per-user
# activity reports. Here it reads the gold tables analytics_daily produced —
# no cross-store stitching, no gzip re-parsing.

# COMMAND ----------
import re
from datetime import timedelta

from pyspark.sql import functions as F
from pyspark.sql.window import Window

dbutils.widgets.text("catalog", "ow_tp")
dbutils.widgets.text("ns", "dev")
dbutils.widgets.text("lookback_days", "30")

catalog = dbutils.widgets.get("catalog")
ns = dbutils.widgets.get("ns").lower()
lookback_days = int(dbutils.widgets.get("lookback_days"))
if not re.fullmatch(r"[a-z0-9_]{1,64}", catalog):
    raise ValueError(f"invalid catalog: {catalog!r}")
if not re.fullmatch(r"[a-z0-9_]{1,32}", ns):
    raise ValueError(f"invalid namespace: {ns!r}")

# COMMAND ----------
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.gold.user_activity_report (
        ns STRING,
        user_id STRING,
        total_actions BIGINT,
        active_days BIGINT,
        activity_rank INT
    )
    """
)

actions = spark.table(f"`{catalog}`.gold.analytics_user_actions_daily").where(
    F.col("ns") == ns
)
max_date = actions.agg(F.max("event_date").alias("m")).collect()[0]["m"]
if max_date is None:
    raise RuntimeError(f"no user actions for ns '{ns}' — run analytics_daily first")

window_start = max_date - timedelta(days=lookback_days - 1)

in_window = actions.where(F.col("event_date") >= F.lit(window_start))

report = (
    in_window.groupBy("user_id")
    .agg(
        F.sum("action_count").alias("total_actions"),
        F.countDistinct("event_date").alias("active_days"),
    )
    .withColumn(
        "activity_rank",
        F.row_number().over(
            Window.orderBy(F.col("total_actions").desc(), F.col("user_id"))
        ),
    )
    .select(F.lit(ns).alias("ns"), "user_id", "total_actions", "active_days", "activity_rank")
)

spark.sql(f"DELETE FROM `{catalog}`.gold.user_activity_report WHERE ns = '{ns}'")
report.write.mode("append").saveAsTable(f"`{catalog}`.gold.user_activity_report")

for row in report.orderBy("activity_rank").limit(5).collect():
    print(
        f"#{row['activity_rank']} {row['user_id']}: {row['total_actions']} actions "
        f"over {row['active_days']} days"
    )
print(f"window: {window_start} .. {max_date}")

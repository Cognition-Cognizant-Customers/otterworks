# Databricks notebook source
# Audit archive weekly — replaces etl/scripts/audit_archive_weekly.py (cron 0 3 * * 0).
#
# The legacy script scanned DynamoDB for events older than 90 days, gzipped
# them to Glacier, deleted the originals, and wrote a compliance report. Here
# retention is a Delta operation: expired events move from bronze.events to
# gold.audit_archive_events (time-travel replaces Glacier restore) and a run
# summary is recorded. The cutoff is anchored to the newest event in the slice
# so the deterministic seeded estate reconciles bit-for-bit regardless of when
# the job runs.

# COMMAND ----------
import re
from datetime import timedelta

from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "ow_tp")
dbutils.widgets.text("ns", "dev")
dbutils.widgets.text("retention_days", "90")

catalog = dbutils.widgets.get("catalog")
ns = dbutils.widgets.get("ns").lower()
retention_days = int(dbutils.widgets.get("retention_days"))
if not re.fullmatch(r"[a-z0-9_]{1,64}", catalog):
    raise ValueError(f"invalid catalog: {catalog!r}")
if not re.fullmatch(r"[a-z0-9_]{1,32}", ns):
    raise ValueError(f"invalid namespace: {ns!r}")

# COMMAND ----------
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.gold.audit_archive_events (
        ns STRING,
        event_id STRING,
        event_type STRING,
        user_id STRING,
        resource_id STRING,
        occurred_at TIMESTAMP,
        archived_cutoff TIMESTAMP
    )
    """
)
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.gold.audit_archive_runs (
        ns STRING,
        retention_days INT,
        cutoff TIMESTAMP,
        archived_events BIGINT,
        retained_events BIGINT
    )
    """
)

events = spark.table(f"`{catalog}`.bronze.events").where(F.col("ns") == ns)
max_ts = events.agg(F.max("occurred_at").alias("m")).collect()[0]["m"]
if max_ts is None:
    raise RuntimeError(f"no events in bronze.events for ns '{ns}' — run analytics_daily first")

cutoff = max_ts - timedelta(days=retention_days)

expired = events.where(F.col("occurred_at") < F.lit(cutoff))
archived = expired.count()
retained = events.count() - archived

# Only rewrite the archive when there is something to archive: a standalone
# re-run (after the expired rows were already moved out of bronze) must not
# wipe the previously archived slice.
if archived > 0:
    spark.sql(f"DELETE FROM `{catalog}`.gold.audit_archive_events WHERE ns = '{ns}'")
    expired.withColumn("archived_cutoff", F.lit(cutoff)).write.mode("append").saveAsTable(
        f"`{catalog}`.gold.audit_archive_events"
    )
    # Like the legacy job's post-archive delete: expired rows leave bronze.events
    # (Delta time travel serves as the Glacier-restore equivalent).
    spark.sql(
        f"""DELETE FROM `{catalog}`.bronze.events
            WHERE ns = '{ns}' AND occurred_at < TIMESTAMP'{cutoff.isoformat(sep=" ")}'"""
    )
spark.sql(f"DELETE FROM `{catalog}`.gold.audit_archive_runs WHERE ns = '{ns}'")
spark.createDataFrame(
    [(ns, retention_days, cutoff, archived, retained)],
    "ns STRING, retention_days INT, cutoff TIMESTAMP, archived_events BIGINT, retained_events BIGINT",
).write.mode("append").saveAsTable(f"`{catalog}`.gold.audit_archive_runs")

print(f"cutoff={cutoff} archived={archived} retained={retained}")

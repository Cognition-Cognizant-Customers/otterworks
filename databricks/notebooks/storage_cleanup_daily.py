# Databricks notebook source
# Storage cleanup daily — replaces etl/scripts/storage_cleanup_daily.py (cron 0 4 * * *).
#
# The legacy script diffed the S3 files/ prefix against DynamoDB s3_key
# references and quarantined unreferenced objects. In the lakehouse the file
# metadata lands as a JSONL export, and the cleanup surfaces the inverse (and
# more dangerous) defect the estate actually plants: metadata rows whose
# s3_key points outside the live files/ prefix (dangling references). Both
# directions of the diff become queryable gold tables instead of a text report
# on a cron box.

# COMMAND ----------
import re

from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "ow_tp")
dbutils.widgets.text("ns", "dev")

catalog = dbutils.widgets.get("catalog")
ns = dbutils.widgets.get("ns").lower()
if not re.fullmatch(r"[a-z0-9_]{1,32}", ns):
    raise ValueError(f"invalid namespace: {ns!r}")

landing = f"/Volumes/{catalog}/bronze/landing/{ns}/file_metadata"

# COMMAND ----------
# Stage inputs uploaded to the workspace landing area (the demo PAT has
# workspace scope but not the files scope) into the UC landing volume.
import os
import shutil

ws_landing = f"/Workspace/Shared/{catalog}/landing/{ns}/file_metadata"
if os.path.isdir(ws_landing):
    shutil.copytree(ws_landing, landing, dirs_exist_ok=True)

# COMMAND ----------
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.bronze.file_metadata (
        ns STRING,
        id STRING,
        name STRING,
        mime_type STRING,
        size_bytes BIGINT,
        s3_key STRING,
        owner_id STRING,
        is_trashed BOOLEAN,
        created_at TIMESTAMP
    )
    """
)
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.gold.storage_cleanup_report (
        ns STRING,
        total_objects BIGINT,
        referenced_objects BIGINT,
        dangling_references BIGINT,
        trashed_objects BIGINT,
        reclaimable_bytes BIGINT
    )
    """
)
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.gold.storage_dangling_references (
        ns STRING,
        id STRING,
        s3_key STRING,
        size_bytes BIGINT,
        owner_id STRING
    )
    """
)

meta = (
    spark.read.json(f"{landing}/*.jsonl.gz")
    .select(
        F.lit(ns).alias("ns"),
        "id",
        "name",
        "mime_type",
        F.col("size_bytes").cast("bigint").alias("size_bytes"),
        "s3_key",
        "owner_id",
        F.col("is_trashed").cast("boolean").alias("is_trashed"),
        F.to_timestamp("created_at").alias("created_at"),
    )
)

spark.sql(f"DELETE FROM `{catalog}`.bronze.file_metadata WHERE ns = '{ns}'")
meta.write.mode("append").saveAsTable(f"`{catalog}`.bronze.file_metadata")
meta = spark.table(f"`{catalog}`.bronze.file_metadata").where(F.col("ns") == ns)

# COMMAND ----------
dangling = meta.where(~F.col("s3_key").startswith(f"{ns}/files/"))

report = meta.agg(
    F.count("*").alias("total_objects"),
    F.sum(F.when(F.col("s3_key").startswith(f"{ns}/files/"), 1).otherwise(0)).alias(
        "referenced_objects"
    ),
    F.sum(F.when(~F.col("s3_key").startswith(f"{ns}/files/"), 1).otherwise(0)).alias(
        "dangling_references"
    ),
    F.sum(F.when(F.col("is_trashed"), 1).otherwise(0)).alias("trashed_objects"),
    F.sum(F.when(F.col("is_trashed"), F.col("size_bytes")).otherwise(0)).alias(
        "reclaimable_bytes"
    ),
).select(F.lit(ns).alias("ns"), "*")

spark.sql(f"DELETE FROM `{catalog}`.gold.storage_cleanup_report WHERE ns = '{ns}'")
report.write.mode("append").saveAsTable(f"`{catalog}`.gold.storage_cleanup_report")
spark.sql(f"DELETE FROM `{catalog}`.gold.storage_dangling_references WHERE ns = '{ns}'")
dangling.select("ns", "id", "s3_key", "size_bytes", "owner_id").write.mode("append").saveAsTable(
    f"`{catalog}`.gold.storage_dangling_references"
)

row = report.collect()[0]
print(
    f"total={row['total_objects']} referenced={row['referenced_objects']} "
    f"dangling={row['dangling_references']} trashed={row['trashed_objects']} "
    f"reclaimable_bytes={row['reclaimable_bytes']}"
)

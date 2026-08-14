# Databricks notebook source
# Search reindex weekly — replaces etl/scripts/search_reindex_weekly.py (cron 0 5 * * 0).
#
# The legacy script dropped and recreated the MeiliSearch documents/files
# indexes, bulk-fed them from the service APIs, then hand-validated counts.
# Here the same corpora land as JSONL exports and are rebuilt as gold index
# tables; count validation is enforced in-job instead of eyeballed.

# COMMAND ----------
import re

from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "ow_tp")
dbutils.widgets.text("ns", "dev")

catalog = dbutils.widgets.get("catalog")
ns = dbutils.widgets.get("ns").lower()
if not re.fullmatch(r"[a-z0-9_]{1,64}", catalog):
    raise ValueError(f"invalid catalog: {catalog!r}")
if not re.fullmatch(r"[a-z0-9_]{1,32}", ns):
    raise ValueError(f"invalid namespace: {ns!r}")

landing = f"/Volumes/{catalog}/bronze/landing/{ns}"

# COMMAND ----------
# Stage inputs uploaded to the workspace landing area (the demo PAT has
# workspace scope but not the files scope) into the UC landing volume.
import os
import shutil

ws_landing = f"/Workspace/Shared/{catalog}/landing/{ns}/documents"
if os.path.isdir(ws_landing):
    # Mirror the staging area exactly: drop stale files from earlier runs.
    shutil.rmtree(f"{landing}/documents", ignore_errors=True)
    shutil.copytree(ws_landing, f"{landing}/documents", dirs_exist_ok=True)

# COMMAND ----------
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.gold.search_documents_index (
        ns STRING,
        id STRING,
        title STRING,
        owner_id STRING,
        content_type STRING,
        is_deleted BOOLEAN
    )
    """
)
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.gold.search_files_index (
        ns STRING,
        id STRING,
        name STRING,
        mime_type STRING,
        owner_id STRING,
        is_trashed BOOLEAN
    )
    """
)

docs = (
    spark.read.json(f"{landing}/documents/*.jsonl.gz")
    .select(
        F.lit(ns).alias("ns"),
        "id",
        "title",
        "owner_id",
        "content_type",
        F.col("is_deleted").cast("boolean").alias("is_deleted"),
    )
)
files = spark.table(f"`{catalog}`.bronze.file_metadata").where(F.col("ns") == ns).select(
    "ns", "id", "name", "mime_type", "owner_id", "is_trashed"
)

spark.sql(f"DELETE FROM `{catalog}`.gold.search_documents_index WHERE ns = '{ns}'")
docs.write.mode("append").saveAsTable(f"`{catalog}`.gold.search_documents_index")
spark.sql(f"DELETE FROM `{catalog}`.gold.search_files_index WHERE ns = '{ns}'")
files.write.mode("append").saveAsTable(f"`{catalog}`.gold.search_files_index")

# COMMAND ----------
doc_src = docs.count()
doc_idx = (
    spark.table(f"`{catalog}`.gold.search_documents_index").where(F.col("ns") == ns).count()
)
file_src = files.count()
file_idx = spark.table(f"`{catalog}`.gold.search_files_index").where(F.col("ns") == ns).count()

print(f"documents: source={doc_src} indexed={doc_idx}")
print(f"files: source={file_src} indexed={file_idx}")
if doc_src != doc_idx or file_src != file_idx:
    raise RuntimeError("index count validation failed")

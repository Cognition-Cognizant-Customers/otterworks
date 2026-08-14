# Databricks notebook source
# CUSTBILL bronze ingest — replaces etl/legacy-extra/jobs/sftp_ingest_poll.ksh.
#
# The legacy job polled an SFTP drop directory, "settled" files by comparing
# byte counts one second apart, then copied them into incoming/ and archive/.
# Here the landing zone is a Unity Catalog volume (atomic PUT — no settle hack)
# and bronze keeps every raw line with its source file for full lineage.

# COMMAND ----------
import re

from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "ow_tp")
dbutils.widgets.text("ns", "dev")

catalog = dbutils.widgets.get("catalog")
ns = dbutils.widgets.get("ns").lower()
if not re.fullmatch(r"[a-z0-9_]{1,32}", ns):
    raise ValueError(f"invalid namespace: {ns!r}")
if not re.fullmatch(r"[a-z0-9_]{1,64}", catalog):
    raise ValueError(f"invalid catalog: {catalog!r}")

landing = f"/Volumes/{catalog}/bronze/landing/{ns}/custbill"

# COMMAND ----------
# Stage inputs uploaded to the workspace landing area (the demo PAT has
# workspace scope but not the files scope) into the UC landing volume.
import os
import shutil

ws_landing = f"/Workspace/Shared/{catalog}/landing/{ns}/custbill"
if os.path.isdir(ws_landing):
    shutil.copytree(ws_landing, landing, dirs_exist_ok=True)

# COMMAND ----------
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.bronze.custbill_raw (
        ns STRING,
        source_file STRING,
        raw_line STRING,
        ingested_at TIMESTAMP
    )
    """
)

df = (
    spark.read.text(f"{landing}/CUSTBILL_*.dat")
    .withColumn("source_file", F.element_at(F.split(F.col("_metadata.file_path"), "/"), -1))
    .select(
        F.lit(ns).alias("ns"),
        F.col("source_file"),
        F.col("value").alias("raw_line"),
        F.current_timestamp().alias("ingested_at"),
    )
)

spark.sql(f"DELETE FROM `{catalog}`.bronze.custbill_raw WHERE ns = '{ns}'")
df.write.mode("append").saveAsTable(f"`{catalog}`.bronze.custbill_raw")

count = spark.sql(
    f"SELECT COUNT(*) AS c FROM `{catalog}`.bronze.custbill_raw WHERE ns = '{ns}'"
).collect()[0]["c"]
print(f"bronze.custbill_raw[{ns}]: {count} raw lines ingested from {landing}")

# Databricks notebook source
# CUSTBILL gold finance summary — replaces etl/legacy-extra/jobs/finance_excel_report.pl.
#
# The legacy Perl job grouped parsed PSV rows by currency|record_type and wrote
# Currency,RecordType,RecordCount,TotalAmount CSV (then copied it to .xls).
# Same aggregation here, as a governed Delta table.

# COMMAND ----------
import re

from pyspark.sql import functions as F

dbutils.widgets.text("catalog", "ow_tp")
dbutils.widgets.text("ns", "dev")

catalog = dbutils.widgets.get("catalog")
ns = dbutils.widgets.get("ns").lower()
if not re.fullmatch(r"[a-z0-9_]{1,32}", ns):
    raise ValueError(f"invalid namespace: {ns!r}")

# COMMAND ----------
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.gold.finance_billing_summary (
        ns STRING,
        currency STRING,
        record_type STRING,
        record_count BIGINT,
        total_amount DECIMAL(14,2)
    )
    """
)

records = spark.table(f"`{catalog}`.silver.custbill_records").where(F.col("ns") == ns)

summary = (
    records.groupBy("currency", "record_type")
    .agg(F.count("*").alias("record_count"), F.sum("amount").alias("total_amount"))
    .select(
        F.lit(ns).alias("ns"),
        "currency",
        F.when(F.col("record_type") == "01", "INVOICE")
        .when(F.col("record_type") == "02", "CREDIT")
        .otherwise(F.concat(F.lit("UNKNOWN("), F.col("record_type"), F.lit(")")))
        .alias("record_type"),
        "record_count",
        F.col("total_amount").cast("decimal(14,2)").alias("total_amount"),
    )
)

spark.sql(f"DELETE FROM `{catalog}`.gold.finance_billing_summary WHERE ns = '{ns}'")
summary.write.mode("append").saveAsTable(f"`{catalog}`.gold.finance_billing_summary")

for row in summary.orderBy("currency", "record_type").collect():
    print(f"{row['currency']},{row['record_type']},{row['record_count']},{row['total_amount']}")

# Databricks notebook source
# CUSTBILL silver parse — replaces etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh.
#
# Fixed-width layout (copybook CBCUST01, 65-byte records):
#   cols 1-10  customer id          cols 49-60 amount PIC 9(10)V99 (implied decimal)
#   cols 11-40 customer name        cols 61-63 currency
#   cols 41-48 billing date YYYYMMDD cols 64-65 record type (01 invoice, 02 credit)
#
# Semantics preserved for clean records: rtrim id/name/currency, cents -> 2dp
# amount, YYYYMMDD -> YYYY-MM-DD. Unlike the legacy parser (which emitted
# garbage for malformed rows and never checked the trailer), invalid rows are
# quarantined with a reason and every file's TRL count is reconciled.

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
    CREATE TABLE IF NOT EXISTS `{catalog}`.silver.custbill_records (
        ns STRING,
        source_file STRING,
        customer_id STRING,
        customer_name STRING,
        billing_date DATE,
        amount DECIMAL(12,2),
        currency STRING,
        record_type STRING
    )
    """
)
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.silver.custbill_quarantine (
        ns STRING,
        source_file STRING,
        raw_line STRING,
        reject_reason STRING
    )
    """
)
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{catalog}`.silver.custbill_file_audit (
        ns STRING,
        source_file STRING,
        trailer_declared BIGINT,
        parsed_ok BIGINT,
        quarantined BIGINT,
        status STRING
    )
    """
)

# COMMAND ----------
raw = spark.table(f"`{catalog}`.bronze.custbill_raw").where(F.col("ns") == ns)

body = raw.where(
    ~F.col("raw_line").startswith("HDR")
    & ~F.col("raw_line").startswith("TRL")
    & (F.trim(F.col("raw_line")) != "")
)

parsed = body.select(
    F.col("ns"),
    F.col("source_file"),
    F.col("raw_line"),
    F.rtrim(F.substring("raw_line", 1, 10)).alias("customer_id"),
    F.rtrim(F.substring("raw_line", 11, 30)).alias("customer_name"),
    F.substring("raw_line", 41, 8).alias("date_str"),
    F.substring("raw_line", 49, 12).alias("amount_str"),
    F.rtrim(F.substring("raw_line", 61, 3)).alias("currency"),
    F.substring("raw_line", 64, 2).alias("record_type"),
)

validated = parsed.withColumn(
    "reject_reason",
    F.when(F.length("raw_line") < 65, F.lit("short_record"))
    .when(~F.col("amount_str").rlike(r"^[0-9]{12}$"), F.lit("non_numeric_amount"))
    .when(F.to_date("date_str", "yyyyMMdd").isNull(), F.lit("invalid_billing_date"))
    .when(~F.col("currency").rlike(r"^[A-Z]{3}$"), F.lit("invalid_currency"))
    .when(~F.col("record_type").isin("01", "02"), F.lit("unknown_record_type"))
    .when(F.col("customer_id") == "", F.lit("missing_customer_id")),
)

good = validated.where(F.col("reject_reason").isNull()).select(
    "ns",
    "source_file",
    "customer_id",
    "customer_name",
    F.to_date("date_str", "yyyyMMdd").alias("billing_date"),
    (F.col("amount_str").cast("decimal(14,0)") / 100).cast("decimal(12,2)").alias("amount"),
    "currency",
    "record_type",
)
bad = validated.where(F.col("reject_reason").isNotNull()).select(
    "ns", "source_file", "raw_line", "reject_reason"
)

spark.sql(f"DELETE FROM `{catalog}`.silver.custbill_records WHERE ns = '{ns}'")
spark.sql(f"DELETE FROM `{catalog}`.silver.custbill_quarantine WHERE ns = '{ns}'")
spark.sql(f"DELETE FROM `{catalog}`.silver.custbill_file_audit WHERE ns = '{ns}'")
good.write.mode("append").saveAsTable(f"`{catalog}`.silver.custbill_records")
bad.write.mode("append").saveAsTable(f"`{catalog}`.silver.custbill_quarantine")

# COMMAND ----------
# Trailer reconciliation: TRL cols 4-13 declare the body record count.
trailers = (
    raw.where(F.col("raw_line").startswith("TRL"))
    .select(
        "source_file",
        F.substring("raw_line", 4, 10).cast("bigint").alias("trailer_declared"),
    )
)
ok_counts = good.groupBy("source_file").agg(F.count("*").alias("parsed_ok"))
bad_counts = bad.groupBy("source_file").agg(F.count("*").alias("quarantined"))

audit = (
    trailers.join(ok_counts, "source_file", "full")
    .join(bad_counts, "source_file", "full")
    .fillna(0, ["trailer_declared", "parsed_ok", "quarantined"])
    .select(
        F.lit(ns).alias("ns"),
        "source_file",
        "trailer_declared",
        "parsed_ok",
        "quarantined",
        F.when(
            F.col("trailer_declared") == F.col("parsed_ok") + F.col("quarantined"),
            F.lit("MATCHED"),
        )
        .otherwise(F.lit("MISMATCH"))
        .alias("status"),
    )
)
audit.write.mode("append").saveAsTable(f"`{catalog}`.silver.custbill_file_audit")

summary = audit.collect()
for row in summary:
    print(
        f"{row['source_file']}: declared={row['trailer_declared']} "
        f"ok={row['parsed_ok']} quarantined={row['quarantined']} {row['status']}"
    )
if any(r["status"] == "MISMATCH" for r in summary):
    raise RuntimeError("trailer count mismatch — see silver.custbill_file_audit")

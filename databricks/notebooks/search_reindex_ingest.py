# Databricks notebook source
# MAGIC %md
# MAGIC # search_reindex_ingest
# MAGIC
# MAGIC Task 1 of `ow_tp_search_reindex` (converted from `etl/scripts/search_reindex_weekly.py`).
# MAGIC
# MAGIC Lands the extracted search corpus in `ow_tp.bronze.search_documents_raw` as the raw
# MAGIC source of truth for the run. Nothing downstream of the source is deleted or replaced
# MAGIC here, so a failed extract can never leave the serving index empty -- which is exactly
# MAGIC what the legacy cron did when it dropped both MeiliSearch indexes up front.
# MAGIC
# MAGIC The landed row counts are validated against the extract manifest: a truncated extract
# MAGIC fails the run instead of being published as a smaller index.

# COMMAND ----------

import json
import re
from functools import reduce

from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

CATALOG = "ow_tp"
BRONZE_TABLE = f"{CATALOG}.bronze.search_documents_raw"
LANDING_ROOT = f"/Volumes/{CATALOG}/bronze/landing"
FILES = {"document": "documents.ndjson", "file": "files.ndjson"}

dbutils.widgets.text("ns", "demo")
dbutils.widgets.text("landing_prefix", "search_reindex")
dbutils.widgets.text("simulate_source_failure", "false")

ns = dbutils.widgets.get("ns").strip()
landing_prefix = dbutils.widgets.get("landing_prefix").strip().strip("/")
simulate_source_failure = dbutils.widgets.get("simulate_source_failure").strip().lower() == "true"

if not re.fullmatch(r"[a-z0-9_]+", ns):
    raise ValueError(f"ns must match [a-z0-9_]+, got {ns!r}")
if not re.fullmatch(r"[a-z0-9_-]+(/[a-z0-9_-]+)*", landing_prefix):
    raise ValueError(
        "landing_prefix must match [a-z0-9_-]+(/[a-z0-9_-]+)*, "
        f"got {landing_prefix!r}"
    )

landing_dir = f"{LANDING_ROOT}/{ns}/{landing_prefix}"


def log(event, **fields):
    print(json.dumps({"logger": "search_reindex.ingest", "event": event, "ns": ns, **fields}))


log("ingest_started", landing_dir=landing_dir, simulate_source_failure=simulate_source_failure)

# COMMAND ----------

# Failure injection for the build-then-swap acceptance check: fail the way the legacy run
# failed (source unreadable) before anything is read, and prove the already-published index
# survives it untouched.
if simulate_source_failure:
    log("source_failure_simulated")
    raise RuntimeError(
        "simulated source failure before reading the extract; "
        "bronze, silver and gold are left exactly as the previous run published them"
    )

# COMMAND ----------

# The extract manifest is written by scripts/tp_databricks/extract_search_sources.py and
# carries the per-entity counts this task reconciles the landed data against.
manifest_text = "".join(row.value for row in spark.read.text(f"{landing_dir}/_manifest.json").collect())
manifest = json.loads(manifest_text)
expected = {entity: int(count) for entity, count in manifest["counts"].items()}
log("manifest_read", expected=expected, extracted_at=manifest["extracted_at"])

if manifest.get("ns") != ns:
    raise ValueError(f"manifest ns {manifest.get('ns')!r} does not match run ns {ns!r}")

# COMMAND ----------

ENVELOPE = StructType([
    StructField("ns", StringType()),
    StructField("entity_type", StringType()),
    StructField("entity_id", StringType()),
    StructField("extracted_at", StringType()),
    StructField("payload", StringType()),
])

frames = []
for entity_type, filename in FILES.items():
    frame = (
        spark.read.text(f"{landing_dir}/{filename}")
        .select(F.from_json("value", ENVELOPE).alias("e"))
        .select("e.*")
        .withColumn("entity_type", F.lit(entity_type))
        .withColumn("extracted_at", F.col("extracted_at").cast("timestamp"))
    )
    frames.append(frame)

landed = reduce(lambda left, right: left.unionByName(right), frames)
landed = landed.filter(F.col("ns") == F.lit(ns)).select(
    "ns", "entity_type", "entity_id", "payload", "extracted_at"
)
landed.cache()

landed_counts = {row["entity_type"]: row["n"] for row in landed.groupBy("entity_type").agg(F.count("*").alias("n")).collect()}
log("landed_counts", counts=landed_counts)

missing_ids = landed.filter(F.col("entity_id").isNull() | (F.trim(F.col("entity_id")) == "")).count()
if missing_ids:
    raise ValueError(f"{missing_ids} landed records have no entity_id; refusing to index unidentifiable rows")

divergent = {e: (landed_counts.get(e, 0), c) for e, c in expected.items() if landed_counts.get(e, 0) != c}
if divergent:
    raise ValueError(f"landed rows do not match the extract manifest (entity: landed, expected): {divergent}")

# COMMAND ----------

# Idempotent by namespace: a rerun replaces this namespace's rows rather than appending,
# so counts stay stable and no entity id is duplicated.
(
    landed.write.format("delta")
    .mode("overwrite")
    .option("replaceWhere", f"ns = '{ns}'")
    .option("overwriteSchema", "false")
    .saveAsTable(BRONZE_TABLE)
)

bronze_counts = {
    row["entity_type"]: row["n"]
    for row in spark.sql(
        f"SELECT entity_type, COUNT(*) AS n FROM {BRONZE_TABLE} WHERE ns = '{ns}' GROUP BY entity_type"
    ).collect()
}
log("bronze_written", table=BRONZE_TABLE, counts=bronze_counts)

if bronze_counts != landed_counts:
    raise ValueError(f"bronze counts {bronze_counts} do not match landed counts {landed_counts}")

dbutils.notebook.exit(json.dumps({"ns": ns, "counts": bronze_counts, "extracted_at": manifest["extracted_at"]}))

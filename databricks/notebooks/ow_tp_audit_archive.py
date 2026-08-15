# Databricks notebook source
# MAGIC %md
# MAGIC # Audit-event retention (`ow_tp_audit_archive`)
# MAGIC
# MAGIC Converted from `etl/scripts/audit_archive_weekly.py` (system cron, Sunday
# MAGIC 03:00 UTC). The legacy job scanned the entire DynamoDB audit table to find
# MAGIC events past a 90-day horizon, wrote a JSONL.gz to Glacier, then batch-deleted
# MAGIC the source rows inside `try: ... except: pass` -- the upload and the delete
# MAGIC were not atomic and neither was checked, so a failed upload still deleted and
# MAGIC a failed delete still reported success.
# MAGIC
# MAGIC This pipeline keeps the legacy selection semantics exactly (`event_ts <
# MAGIC cutoff`, cutoff = `run_date - retention_days`, exclusive) and changes how the
# MAGIC work is done:
# MAGIC
# MAGIC | Legacy deficiency | Here |
# MAGIC |---|---|
# MAGIC | full-table scan | retention predicate pushed down on a table clustered by `(ns, event_ts)` |
# MAGIC | delete before any durable copy is proven | archive -> verify -> delete; the delete is skipped and the run fails if verification fails |
# MAGIC | credentials in `etl/config.ini` | no credentials: Unity Catalog governs access |
# MAGIC | `print()` logging | `logging` with a structured run summary |
# MAGIC | `except: pass` | failures raise and fail the run |
# MAGIC | retention only in the source | `retention_days` is a job parameter, stamped on every archived row and manifest row |
# MAGIC | no idempotency | every write is a MERGE keyed on `(ns, event_id)` / `(ns, run_date)`, and counts are derived from state, so a re-run is a no-op |
# MAGIC | no alerting | job-level failure notification and duration health rule (`jobs_audit_archive.tf`) |

# COMMAND ----------

import json
import logging
from datetime import date, datetime, timedelta, timezone

dbutils.widgets.text("ns", "demo", "namespace")
dbutils.widgets.text("run_date", "", "execution date (UTC, empty = today)")
dbutils.widgets.text("retention_days", "90", "retention horizon in days")
dbutils.widgets.text("catalog", "ow_tp", "Unity Catalog catalog")
dbutils.widgets.text("source_path", "/Volumes/ow_tp/bronze/landing", "landing volume root")

ns = dbutils.widgets.get("ns").strip()
catalog = dbutils.widgets.get("catalog").strip()
retention_days = int(dbutils.widgets.get("retention_days"))
source_root = dbutils.widgets.get("source_path").strip().rstrip("/")
run_date_param = dbutils.widgets.get("run_date").strip()
run_date = date.fromisoformat(run_date_param) if run_date_param else datetime.now(tz=timezone.utc).date()

if not ns.isidentifier():
    raise ValueError(f"ns must be an identifier, got {ns!r}")
if not catalog.startswith("ow_tp"):
    raise ValueError(f"catalog must be ow_tp-prefixed in this shared workspace, got {catalog!r}")
if retention_days <= 0:
    raise ValueError(f"retention_days must be positive, got {retention_days}")

# The legacy cutoff is midnight UTC of (execution date - retention_days), compared
# with a strict `<` against an ISO-8601 string, i.e. exclusive: an event exactly on
# the cutoff is retained. Same semantics here.
cutoff = datetime.combine(run_date - timedelta(days=retention_days), datetime.min.time())

spark.conf.set("spark.sql.session.timeZone", "UTC")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ow_tp.audit_archive")

BRONZE = f"{catalog}.bronze.audit_events_raw"
SILVER = f"{catalog}.silver.audit_events_archived"
GOLD = f"{catalog}.gold.audit_archive_manifest"
SOURCE = f"{source_root}/{ns}/audit_archive/audit_events.jsonl"
CUTOFF_SQL = f"TIMESTAMP '{cutoff.strftime('%Y-%m-%d %H:%M:%S')}'"
PAST_CUTOFF = f"ns = '{ns}' AND event_ts < {CUTOFF_SQL}"

log.info(
    "run parameters %s",
    json.dumps({
        "ns": ns,
        "run_date": run_date.isoformat(),
        "retention_days": retention_days,
        "cutoff_ts": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "catalog": catalog,
        "source": SOURCE,
    }),
)


def scalar(statement: str):
    return spark.sql(statement).collect()[0][0]

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Ingest
# MAGIC Typed read of the landed audit events with an explicit schema (the legacy job
# MAGIC took whatever DynamoDB returned). The anti-join is what keeps retention
# MAGIC durable: an event already archived and purged is never resurrected into
# MAGIC bronze by a later ingest.

# COMMAND ----------

SOURCE_SCHEMA = (
    "ns string, event_id string, timestamp string, actor string, action string, "
    "target_id string, raw_payload string, ingested_at string"
)
TS_FORMAT = "yyyy-MM-dd'T'HH:mm:ss'Z'"

ingest_metrics = spark.sql(f"""
MERGE INTO {BRONZE} AS t
USING (
  SELECT s.ns,
         s.event_id,
         to_timestamp(s.timestamp, "{TS_FORMAT}") AS event_ts,
         s.actor,
         s.action,
         s.target_id,
         s.raw_payload,
         coalesce(to_timestamp(s.ingested_at, "{TS_FORMAT}"), current_timestamp()) AS ingested_at
  FROM (
    SELECT * FROM read_files('{SOURCE}', format => 'json', schema => '{SOURCE_SCHEMA}')
    WHERE ns = '{ns}'
  ) AS s
  LEFT ANTI JOIN {SILVER} AS a
    ON a.ns = s.ns AND a.event_id = s.event_id
) AS s
ON t.ns = s.ns AND t.event_id = s.event_id
WHEN NOT MATCHED THEN INSERT *
""").collect()[0].asDict()
log.info("ingest merge metrics %s", json.dumps(ingest_metrics, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Select candidates
# MAGIC Predicate pushdown against the clustered retention column, instead of the
# MAGIC legacy paginated full-table scan with a client-side filter.

# COMMAND ----------

bronze_candidates = scalar(f"SELECT count(*) FROM {BRONZE} WHERE {PAST_CUTOFF}")
log.info("candidates still in bronze past cutoff: %d", bronze_candidates)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Archive
# MAGIC MERGE, not append: one row per `(ns, event_id)`, so a re-run never
# MAGIC double-archives.

# COMMAND ----------

archive_metrics = spark.sql(f"""
MERGE INTO {SILVER} AS t
USING (
  SELECT ns, event_id, event_ts, actor, action, target_id, raw_payload,
         current_timestamp()           AS archived_at,
         {retention_days}              AS retention_days,
         {CUTOFF_SQL}                  AS cutoff_ts,
         DATE '{run_date.isoformat()}' AS run_date
  FROM {BRONZE}
  WHERE {PAST_CUTOFF}
) AS s
ON t.ns = s.ns AND t.event_id = s.event_id
WHEN NOT MATCHED THEN INSERT *
""").collect()[0].asDict()
log.info("archive merge metrics %s", json.dumps(archive_metrics, default=str))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Verify before deleting anything
# MAGIC The step the legacy job did not have. Counts are derived from table state,
# MAGIC not from this run's delta, so they are identical on a re-run.

# COMMAND ----------

archived_count = scalar(f"SELECT count(*) FROM {SILVER} WHERE {PAST_CUTOFF}")

# Candidates that exist in bronze but have no archive row: must be zero.
unarchived_count = scalar(f"""
SELECT count(*) FROM (
  SELECT ns, event_id FROM {BRONZE} WHERE {PAST_CUTOFF}
) AS b
LEFT ANTI JOIN {SILVER} AS a ON a.ns = b.ns AND a.event_id = b.event_id
""")

# Read the archive back rather than trusting the write: every archived row must be
# selectable with its payload and provenance intact.
readable_count = scalar(f"""
SELECT count(*) FROM {SILVER}
WHERE {PAST_CUTOFF}
  AND event_id IS NOT NULL
  AND raw_payload IS NOT NULL
  AND archived_at IS NOT NULL
  AND retention_days = {retention_days}
""")

candidate_count = archived_count + unarchived_count
verified = unarchived_count == 0 and archived_count == candidate_count and readable_count == archived_count

log.info(
    "verification %s",
    json.dumps({
        "candidate_count": candidate_count,
        "archived_count": archived_count,
        "unarchived_count": unarchived_count,
        "readable_count": readable_count,
        "verified": verified,
    }),
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Purge the source, only once the archive is verified

# COMMAND ----------

if verified and archived_count > 0:
    purge_metrics = spark.sql(f"""
    DELETE FROM {BRONZE}
    WHERE {PAST_CUTOFF}
      AND event_id IN (SELECT event_id FROM {SILVER} WHERE {PAST_CUTOFF})
    """).collect()
    log.info("purge metrics %s", json.dumps([r.asDict() for r in purge_metrics], default=str))
else:
    log.warning("archive not verified (or nothing to archive): source rows left in place")

# Purged events are those with an archive row and no bronze row left.
deleted_count = scalar(f"""
SELECT count(*) FROM (
  SELECT ns, event_id FROM {SILVER} WHERE {PAST_CUTOFF}
) AS a
LEFT ANTI JOIN {BRONZE} AS b ON b.ns = a.ns AND b.event_id = a.event_id
""")

# The archive must still be readable after the purge -- the legacy failure mode
# was losing the source with nothing durable to show for it.
post_purge_readable = scalar(f"SELECT count(*) FROM {SILVER} WHERE {PAST_CUTOFF}")
if post_purge_readable != archived_count:
    raise RuntimeError(
        f"archive readback changed across the purge: {archived_count} -> {post_purge_readable}"
    )
if deleted_count > 0 and not verified:
    raise RuntimeError(f"{deleted_count} rows purged without a verified archive")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Manifest
# MAGIC Replaces the compliance `report.json` the legacy job wrote to S3 and nothing
# MAGIC ever read. One row per `(ns, run_date)`: MERGE, so a re-run updates in place.

# COMMAND ----------

spark.sql(f"""
MERGE INTO {GOLD} AS t
USING (
  SELECT '{ns}'                        AS ns,
         DATE '{run_date.isoformat()}' AS run_date,
         {CUTOFF_SQL}                  AS cutoff_ts,
         CAST({candidate_count} AS BIGINT) AS candidate_count,
         CAST({archived_count} AS BIGINT)  AS archived_count,
         CAST({deleted_count} AS BIGINT)   AS deleted_count,
         {str(verified).lower()}       AS verified,
         CAST({retention_days} AS INT) AS retention_days,
         current_timestamp()           AS generated_at
) AS s
ON t.ns = s.ns AND t.run_date = s.run_date
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *
""")

summary = {
    "ns": ns,
    "run_date": run_date.isoformat(),
    "cutoff_ts": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "retention_days": retention_days,
    "candidate_count": candidate_count,
    "archived_count": archived_count,
    "deleted_count": deleted_count,
    "verified": verified,
    "newly_archived_this_run": archive_metrics.get("num_inserted_rows"),
    "newly_ingested_this_run": ingest_metrics.get("num_inserted_rows"),
}
log.info("run summary %s", json.dumps(summary))

if not verified:
    raise RuntimeError(f"audit archive verification failed: {json.dumps(summary)}")

dbutils.notebook.exit(json.dumps(summary))

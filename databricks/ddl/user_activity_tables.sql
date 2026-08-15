-- Unity Catalog tables for the converted user-activity job
-- (etl/scripts/user_activity_daily.py -> ow_tp_user_activity).
--
-- Idempotent: every statement is CREATE ... IF NOT EXISTS, so applying this file
-- repeatedly is a no-op. Demo state is per-namespace, so every table carries ns
-- and re-running the pipeline replaces only its own (ns, date) partition.
--
-- Single source of truth for the DDL: the notebook applies this same file from
-- the landing volume (parameter ddl_path), and scripts/tp_databricks/recon_user_activity.py
-- applies it from the repo. It is never duplicated inline.
--
-- Table names are catalog-relative (schema.table) so the file follows whichever
-- catalog the caller sets (`catalog_name` in Terraform, `USE CATALOG` / the SQL
-- API's catalog field here); it never hardcodes one catalog while the job writes
-- to another.

CREATE TABLE IF NOT EXISTS bronze.user_activity_raw (
  ns            STRING  COMMENT 'demo namespace; the isolation boundary for per-run state',
  user_id       STRING  COMMENT "user the metric belongs to; '*' on the all-users rows sourced from the analytics aggregate",
  activity_date DATE    COMMENT 'date the metric was measured on',
  metric        STRING  COMMENT 'event type (document.created, ...), hour bucket (hour.HH), last_event_epoch, or an analytics summary column name',
  value         DOUBLE  COMMENT 'metric value: a count, or epoch seconds for last_event_epoch',
  source        STRING  COMMENT 'events_by_type | events_by_hour | events_last_ts | analytics_summary',
  ingested_at   TIMESTAMP COMMENT 'ingest time of the landing read'
)
USING DELTA
PARTITIONED BY (ns)
COMMENT 'Per-user source rows for the daily activity report: the per-user event data plus the upstream analytics aggregate the legacy script consumed blindly.';

CREATE TABLE IF NOT EXISTS silver.user_activity_daily (
  ns                STRING    COMMENT 'demo namespace',
  user_id           STRING    COMMENT 'user',
  activity_date     DATE      COMMENT 'activity date',
  documents_touched BIGINT    COMMENT 'document.* events attributed to the user on that date',
  files_touched     BIGINT    COMMENT 'file.* events attributed to the user on that date',
  other_events      BIGINT    COMMENT 'remaining events (user.*, quota.*)',
  events            BIGINT    COMMENT 'all events attributed to the user on that date',
  active_hours      INT       COMMENT 'distinct hour buckets with at least one event',
  last_active_ts    TIMESTAMP COMMENT 'latest event timestamp on that date',
  updated_at        TIMESTAMP COMMENT 'pipeline write time'
)
USING DELTA
PARTITIONED BY (ns)
COMMENT 'One typed row per (ns, user_id, activity_date), replacing the legacy pandas in-memory aggregation.';

CREATE TABLE IF NOT EXISTS gold.user_activity_report (
  ns                    STRING    COMMENT 'demo namespace',
  report_date           DATE      COMMENT 'report date; the legacy ds',
  user_id               STRING    COMMENT 'user',
  documents_touched     BIGINT    COMMENT 'document.* events over the lookback window',
  files_touched         BIGINT    COMMENT 'file.* events over the lookback window',
  events                BIGINT    COMMENT 'all events over the lookback window; the legacy total_actions',
  last_active_ts        TIMESTAMP COMMENT 'latest event timestamp in the window',
  active_days           INT       COMMENT 'dates in the window with activity; the legacy active_days',
  upstream_summary_date DATE      COMMENT 'latest report_date present in the upstream analytics aggregate at run time',
  upstream_fresh        BOOLEAN   COMMENT 'freshness verdict: the fact the legacy 02:00/05:00 cron ordering could not express',
  generated_at          TIMESTAMP COMMENT 'pipeline write time'
)
USING DELTA
PARTITIONED BY (ns)
COMMENT 'The report the legacy job shipped to admin-service, plus the upstream-freshness fact it never checked.';

-- Run-level audit trail: replaces the legacy print() logging and the silent
-- `except: pass`, and records the freshness verdict for every run, including
-- refused ones.
CREATE TABLE IF NOT EXISTS gold.user_activity_run_log (
  ns                    STRING    COMMENT 'demo namespace',
  run_ts                TIMESTAMP COMMENT 'run start',
  report_date           DATE      COMMENT 'report date the run targeted',
  stage                 STRING    COMMENT 'freshness_gate | pipeline',
  upstream_summary_date DATE      COMMENT 'latest upstream report_date seen',
  upstream_fresh        BOOLEAN   COMMENT 'freshness verdict',
  status                STRING    COMMENT 'ok | refused_stale_upstream | refused_missing_upstream',
  detail                STRING    COMMENT 'human-readable reason, always populated on refusal',
  rows_written          BIGINT    COMMENT 'gold rows written by the run (0 on refusal)'
)
USING DELTA
PARTITIONED BY (ns)
COMMENT 'Observable run history for the converted user-activity job.';

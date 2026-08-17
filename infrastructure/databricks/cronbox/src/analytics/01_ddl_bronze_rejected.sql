-- cron-analytics DDL: Unity Catalog object owned by the analytics_daily rewrite.
-- Replaces the S3 + Postgres sinks of etl/scripts/analytics_daily.py (452 LOC).
-- Serverless SQL warehouse only; no cluster, no job compute.
CREATE TABLE IF NOT EXISTS ow_tp.bronze.cronbox_events_rejected (
  namespace STRING,
  report_date DATE,
  source STRING,
  source_id STRING,
  raw_body STRING COMMENT 'Unparseable body, preserved for operator triage',
  raw_body_b64 STRING,
  reason STRING COMMENT 'unparseable_json_body or invalid_utf8_body',
  rejected_at TIMESTAMP)
USING DELTA
COMMENT 'Bronze: bodies the legacy job silently swallowed in a bare except, now attributed';

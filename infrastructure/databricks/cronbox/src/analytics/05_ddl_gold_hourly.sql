-- cron-analytics DDL: Unity Catalog object owned by the analytics_daily rewrite.
-- Replaces the S3 + Postgres sinks of etl/scripts/analytics_daily.py (452 LOC).
-- Serverless SQL warehouse only; no cluster, no job compute.
CREATE TABLE IF NOT EXISTS ow_tp.gold.analytics_hourly_breakdown (
  report_date DATE,
  namespace STRING,
  event_hour STRING,
  event_type STRING,
  event_count BIGINT,
  updated_at TIMESTAMP)
USING DELTA
COMMENT 'Gold: replaces analytics/daily/**/hourly_breakdown.json.gz. Public interface consumed by cron-activity';

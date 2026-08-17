-- cron-analytics DDL: Unity Catalog object owned by the analytics_daily rewrite.
-- Replaces the S3 + Postgres sinks of etl/scripts/analytics_daily.py (452 LOC).
-- Serverless SQL warehouse only; no cluster, no job compute.
CREATE TABLE IF NOT EXISTS ow_tp.gold.analytics_daily_top_users (
  report_date DATE,
  namespace STRING,
  user_id STRING COMMENT 'Resolved user id, including the literal unknown',
  action_counts MAP<STRING, BIGINT> COMMENT 'event_type to count, mirrors the legacy actions object',
  total_actions BIGINT,
  user_rank INT COMMENT '1-based rank by total_actions desc, first_seq asc; top 100 only',
  first_seq BIGINT COMMENT 'Lowest extraction sequence for the user; reproduces the legacy stable-sort tie order',
  updated_at TIMESTAMP)
USING DELTA
COMMENT 'Gold: replaces analytics/daily/**/top_users.jsonl.gz. Public interface consumed by cron-activity';

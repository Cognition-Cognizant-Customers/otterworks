-- cron-analytics DDL: Unity Catalog object owned by the analytics_daily rewrite.
-- Replaces the S3 + Postgres sinks of etl/scripts/analytics_daily.py (452 LOC).
-- Serverless SQL warehouse only; no cluster, no job compute.
CREATE TABLE IF NOT EXISTS ow_tp.silver.cronbox_events (
  namespace STRING,
  report_date DATE COMMENT 'ds the event is aggregated under; DynamoDB events use their own event_date',
  source STRING,
  source_id STRING,
  source_seq BIGINT,
  event_id STRING,
  event_type STRING COMMENT 'eventType, falling back to event_type, else the literal unknown',
  resolved_user_id STRING COMMENT 'First non-empty of ownerId, editedBy, authorId, deletedBy, userId; else the literal unknown',
  resolved_user_field STRING COMMENT 'Which precedence field supplied resolved_user_id, NULL when unknown',
  event_hour STRING COMMENT 'Two-digit hour parsed from timestamp, 00 when absent or unparseable',
  event_timestamp STRING COMMENT 'Event timestamp verbatim',
  document_id STRING,
  file_id STRING,
  size_bytes BIGINT,
  title STRING COMMENT 'Multi-byte content carried through without escaping or transliteration',
  name STRING,
  processed_at TIMESTAMP)
USING DELTA
COMMENT 'Silver: one row per accepted analytics event, user resolution and hour bucketing applied';

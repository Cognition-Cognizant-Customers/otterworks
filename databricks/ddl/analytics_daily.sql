-- Unity Catalog tables for the converted `analytics_daily.py` cron.
--
-- Single source of truth for the unit's DDL: the local runner
-- (scripts/tp_databricks/pipeline_analytics_daily.py) reads this file straight from the
-- repo, and the job task reads the copy uploaded to the landing volume, so the statement
-- text the job runs is byte-identical to the reviewed one here.
--
-- All DDL is idempotent (CREATE TABLE IF NOT EXISTS): the legacy script had no notion of
-- a re-runnable load, and every table here is re-created and re-loaded per `ns` slice.
-- `${catalog}` is substituted by the runner (default `ow_tp`); statements are separated
-- by semicolons.

CREATE TABLE IF NOT EXISTS ${catalog}.bronze.analytics_events_raw (
  ns          STRING    COMMENT 'demo namespace this row belongs to; every load replaces exactly one ns slice',
  event_id    STRING    COMMENT 'event identity as ingested; NULL when the source record carried none',
  event_type  STRING    COMMENT 'raw event type, e.g. document.created',
  user_id     STRING    COMMENT 'actor, as ingested',
  document_id STRING    COMMENT 'resource id when the event type is document.*',
  file_id     STRING    COMMENT 'resource id when the event type is file.*',
  event_ts    TIMESTAMP COMMENT 'parsed event timestamp; NULL when unparseable (the row is then quarantined)',
  source      STRING    COMMENT 'sqs | dynamodb | s3 -- which legacy extract path the record arrived on',
  raw_payload STRING    COMMENT 'the source record verbatim, so a rejected row is never lost',
  ingested_at TIMESTAMP COMMENT 'load time of this bronze batch'
)
USING DELTA
COMMENT 'Events as ingested by ow_tp_analytics_daily. Replaces the in-memory pandas DataFrame the legacy cron built from SQS + DynamoDB and never persisted.';

CREATE TABLE IF NOT EXISTS ${catalog}.silver.analytics_events (
  ns          STRING    COMMENT 'demo namespace',
  event_id    STRING    COMMENT 'one row per event_id, deduplicated',
  event_type  STRING,
  user_id     STRING,
  document_id STRING,
  file_id     STRING,
  event_ts    TIMESTAMP COMMENT 'typed, non-NULL: unparseable timestamps are quarantined instead',
  source      STRING,
  ingested_at TIMESTAMP
)
USING DELTA
COMMENT 'Deduplicated, typed events. silver + silver.analytics_events_rejects always equals bronze.analytics_events_raw for a namespace -- nothing is dropped silently.';

CREATE TABLE IF NOT EXISTS ${catalog}.silver.analytics_events_rejects (
  ns            STRING    COMMENT 'demo namespace',
  event_id      STRING    COMMENT 'may be NULL: that is one of the reject reasons',
  reject_reason STRING    COMMENT 'missing_event_id | invalid_event_ts | missing_event_type | duplicate_event_id',
  source        STRING,
  raw_payload   STRING    COMMENT 'the source record verbatim',
  rejected_at   TIMESTAMP
)
USING DELTA
COMMENT 'Quarantine for events silver cannot accept, with a reason per row. Replaces the legacy `except: pass` that discarded malformed messages with no record.';

CREATE TABLE IF NOT EXISTS ${catalog}.gold.analytics_daily_summary (
  ns           STRING  COMMENT 'demo namespace',
  summary_date DATE    COMMENT 'UTC date of event_ts',
  hour         INT     COMMENT 'UTC hour of event_ts (0-23)',
  user_id      STRING,
  document_id  STRING,
  file_id      STRING,
  event_type   STRING,
  event_count  BIGINT  COMMENT 'exact integer count; no floating point anywhere in the aggregate'
)
USING DELTA
COMMENT 'The aggregate the legacy cron wrote to S3 gzip JSON and upserted into Postgres, at the same grain, computed set-based in SQL instead of a row-by-row pandas loop.';

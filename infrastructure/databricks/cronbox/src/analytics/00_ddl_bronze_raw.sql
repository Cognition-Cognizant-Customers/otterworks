-- cron-analytics DDL: Unity Catalog object owned by the analytics_daily rewrite.
-- Replaces the S3 + Postgres sinks of etl/scripts/analytics_daily.py (452 LOC).
-- Serverless SQL warehouse only; no cluster, no job compute.
CREATE TABLE IF NOT EXISTS ow_tp.bronze.cronbox_events_raw (
  namespace STRING COMMENT 'Fixture/run namespace the batch was extracted for',
  report_date DATE COMMENT 'Business date the batch is attributed to (legacy ds)',
  source STRING COMMENT 'sqs or dynamodb',
  source_id STRING COMMENT 'SQS MessageId or DynamoDB event_id',
  source_seq BIGINT COMMENT 'Extraction order within the batch; reproduces the legacy DataFrame row order used for tie-breaks',
  source_event_date STRING COMMENT 'DynamoDB event_date attribute verbatim; NULL for SQS',
  raw_body STRING COMMENT 'Message body carried through verbatim as UTF-8 text; NULL when the body was not decodable',
  raw_body_b64 STRING COMMENT 'Base64 of the original bytes when raw_body could not be decoded as UTF-8',
  decode_error STRING COMMENT 'Extractor-side decoding failure reason, NULL when the body decoded cleanly',
  landed_file STRING COMMENT 'Landing volume file the record arrived in',
  ingested_at TIMESTAMP)
USING DELTA
COMMENT 'Bronze: raw analytics event payloads as landed from SQS and DynamoDB';

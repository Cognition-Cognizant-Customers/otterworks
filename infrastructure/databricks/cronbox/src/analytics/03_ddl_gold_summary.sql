-- cron-analytics DDL: Unity Catalog object owned by the analytics_daily rewrite.
-- Replaces the S3 + Postgres sinks of etl/scripts/analytics_daily.py (452 LOC).
-- Serverless SQL warehouse only; no cluster, no job compute.
CREATE TABLE IF NOT EXISTS ow_tp.gold.analytics_daily_summary (
  report_date DATE COMMENT 'Business date; one row per (report_date, namespace)',
  namespace STRING,
  active_users INT COMMENT 'Distinct resolved users excluding the literal unknown',
  active_documents INT COMMENT 'Distinct documentId over document_created and document_edited',
  active_files INT COMMENT 'Distinct fileId over file_uploaded, file_shared and file_deleted',
  total_events BIGINT,
  documents_created INT,
  documents_edited INT,
  comments_added INT,
  files_uploaded INT,
  files_shared INT,
  files_deleted INT,
  bytes_uploaded BIGINT COMMENT 'Sum of sizeBytes over file_uploaded events',
  updated_at TIMESTAMP COMMENT 'Write clock; excluded from parity comparison like the legacy Postgres NOW()')
USING DELTA
COMMENT 'Gold: replaces Postgres analytics_daily_summary. Public interface consumed by cron-activity';

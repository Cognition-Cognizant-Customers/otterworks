CREATE TABLE IF NOT EXISTS ow_tp.bronze.cronbox_activity_history_summary (
  namespace STRING, landing_ds DATE, report_date DATE, active_users INT, active_documents INT,
  active_files INT, total_events BIGINT, documents_created INT, documents_edited INT,
  comments_added INT, files_uploaded INT, files_shared INT, files_deleted INT,
  bytes_uploaded BIGINT, source STRING, source_line BIGINT, parse_error STRING,
  raw_line STRING, landed_file STRING,
  ingested_at TIMESTAMP
) USING DELTA;

CREATE TABLE IF NOT EXISTS ow_tp.bronze.cronbox_activity_history_top_users (
  namespace STRING, landing_ds DATE, report_date DATE, user_id STRING, total BIGINT, actions_json STRING,
  source_object STRING, source_line BIGINT, raw_line STRING, parse_error STRING,
  landed_file STRING, ingested_at TIMESTAMP
) USING DELTA;

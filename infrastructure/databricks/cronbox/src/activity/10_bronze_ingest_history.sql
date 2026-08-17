MERGE INTO ow_tp.bronze.cronbox_activity_history_summary AS t
USING (
  SELECT
    namespace, CAST(landing_ds AS DATE) AS landing_ds, CAST(report_date AS DATE) AS report_date,
    CAST(active_users AS INT) AS active_users, CAST(active_documents AS INT) AS active_documents,
    CAST(active_files AS INT) AS active_files, CAST(total_events AS BIGINT) AS total_events,
    CAST(documents_created AS INT) AS documents_created, CAST(documents_edited AS INT) AS documents_edited,
    CAST(comments_added AS INT) AS comments_added, CAST(files_uploaded AS INT) AS files_uploaded,
    CAST(files_shared AS INT) AS files_shared, CAST(files_deleted AS INT) AS files_deleted,
    CAST(bytes_uploaded AS BIGINT) AS bytes_uploaded, source, CAST(source_line AS BIGINT) AS source_line,
    parse_error, raw_line, _metadata.file_path AS landed_file, current_timestamp() AS ingested_at
  FROM read_files(
    '/Volumes/ow_tp/bronze/landing/cronbox/user-activity',
    format => 'json',
    recursiveFileLookup => true,
    schemaHints => 'kind STRING, namespace STRING, landing_ds STRING, report_date STRING, active_users INT, active_documents INT, active_files INT, total_events BIGINT, documents_created INT, documents_edited INT, comments_added INT, files_uploaded INT, files_shared INT, files_deleted INT, bytes_uploaded BIGINT, source STRING, source_line BIGINT, parse_error STRING, raw_line STRING'
  )
  WHERE kind = 'daily_summary' AND namespace = :ns AND CAST(landing_ds AS DATE) = DATE(:ds)
) AS s
ON t.namespace = s.namespace AND t.landing_ds = s.landing_ds AND t.report_date = s.report_date
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

MERGE INTO ow_tp.bronze.cronbox_activity_history_top_users AS t
USING (
  SELECT
    namespace, CAST(landing_ds AS DATE) AS landing_ds, CAST(report_date AS DATE) AS report_date,
    user_id, CAST(total AS BIGINT) AS total, actions_json, source_object,
    CAST(source_line AS BIGINT) AS source_line, raw_line, parse_error,
    _metadata.file_path AS landed_file, current_timestamp() AS ingested_at
  FROM read_files(
    '/Volumes/ow_tp/bronze/landing/cronbox/user-activity',
    format => 'json',
    recursiveFileLookup => true,
    schemaHints => 'kind STRING, namespace STRING, landing_ds STRING, report_date STRING, user_id STRING, total BIGINT, actions_json STRING, source_object STRING, source_line BIGINT, raw_line STRING, parse_error STRING'
  )
  WHERE kind = 'top_user' AND namespace = :ns AND CAST(landing_ds AS DATE) = DATE(:ds)
) AS s
ON t.namespace = s.namespace AND t.landing_ds = s.landing_ds
 AND t.report_date = s.report_date AND t.source_object = s.source_object
 AND t.source_line = s.source_line
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

-- cron-analytics gold summary: replaces the Postgres INSERT ... ON CONFLICT upsert.
-- GROUP BY keeps the empty-input case a true no-op: with no silver rows for the
-- run date the source has zero rows, so no all-zero row is ever published and
-- any previously published row is left untouched.
MERGE INTO ow_tp.gold.analytics_daily_summary AS t
USING (
  SELECT
    report_date,
    namespace,
    CAST(COUNT(DISTINCT CASE WHEN resolved_user_id <> 'unknown' THEN resolved_user_id END) AS INT) AS active_users,
    CAST(COUNT(DISTINCT CASE WHEN event_type IN ('document_created', 'document_edited') THEN document_id END) AS INT) AS active_documents,
    CAST(COUNT(DISTINCT CASE WHEN event_type IN ('file_uploaded', 'file_shared', 'file_deleted') THEN file_id END) AS INT) AS active_files,
    COUNT(*) AS total_events,
    CAST(COUNT_IF(event_type = 'document_created') AS INT) AS documents_created,
    CAST(COUNT_IF(event_type = 'document_edited') AS INT) AS documents_edited,
    CAST(COUNT_IF(event_type = 'comment_added') AS INT) AS comments_added,
    CAST(COUNT_IF(event_type = 'file_uploaded') AS INT) AS files_uploaded,
    CAST(COUNT_IF(event_type = 'file_shared') AS INT) AS files_shared,
    CAST(COUNT_IF(event_type = 'file_deleted') AS INT) AS files_deleted,
    COALESCE(SUM(CASE WHEN event_type = 'file_uploaded' THEN COALESCE(size_bytes, 0) END), 0) AS bytes_uploaded,
    current_timestamp() AS updated_at
  FROM ow_tp.silver.cronbox_events
  WHERE namespace = :ns AND report_date = :ds
  GROUP BY report_date, namespace
) AS s
ON t.report_date = s.report_date AND t.namespace = s.namespace
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

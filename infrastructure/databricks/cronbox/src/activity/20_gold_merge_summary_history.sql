MERGE INTO ow_tp.gold.analytics_daily_summary t
USING (
  SELECT namespace, report_date, active_users, active_documents, active_files,
         total_events, documents_created, documents_edited, comments_added,
         files_uploaded, files_shared, files_deleted, bytes_uploaded
  FROM ow_tp.bronze.cronbox_activity_history_summary
  WHERE namespace = :ns AND report_date < DATE(:ds)
) s ON t.namespace = s.namespace AND t.report_date = s.report_date
WHEN MATCHED THEN UPDATE SET
  t.active_users = s.active_users, t.active_documents = s.active_documents,
  t.active_files = s.active_files, t.total_events = s.total_events,
  t.documents_created = s.documents_created, t.documents_edited = s.documents_edited,
  t.comments_added = s.comments_added, t.files_uploaded = s.files_uploaded,
  t.files_shared = s.files_shared, t.files_deleted = s.files_deleted,
  t.bytes_uploaded = s.bytes_uploaded, t.updated_at = current_timestamp()
WHEN NOT MATCHED THEN INSERT (
  namespace, report_date, active_users, active_documents, active_files, total_events,
  documents_created, documents_edited, comments_added, files_uploaded, files_shared,
  files_deleted, bytes_uploaded, updated_at
) VALUES (
  s.namespace, s.report_date, s.active_users, s.active_documents, s.active_files,
  s.total_events, s.documents_created, s.documents_edited, s.comments_added,
  s.files_uploaded, s.files_shared, s.files_deleted, s.bytes_uploaded, current_timestamp()
);

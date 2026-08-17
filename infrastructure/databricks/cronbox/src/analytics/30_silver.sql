-- cron-analytics silver: one row per accepted event, with the legacy five-field
-- user precedence chain, the legacy hour bucketing, and the legacy run-date
-- boundary rule for DynamoDB events applied in-platform.
--
-- report_date on bronze is the batch date. DynamoDB rows are scanned with
-- begins_with(event_date, ds) by the extractor and land in full, so the
-- adjacent-day events stay auditable in bronze and are excluded here.
--
-- Acceptance is the exact complement of the reject predicate in
-- 20_bronze_reject.sql. from_json runs in PERMISSIVE mode, so an unparseable
-- body yields a struct of all-NULL fields rather than a NULL struct; the
-- _corrupt_record field is what actually distinguishes it, and it also rejects
-- JSON that is valid but not an object. An empty body parses to a NULL struct.
--
-- The hour is taken textually from the ISO timestamp rather than through a
-- timestamp cast, so the result is independent of the warehouse session
-- timezone and matches the legacy `datetime.fromisoformat(...).hour`, which
-- also reads the hour as written rather than converting to UTC.
MERGE INTO ow_tp.silver.cronbox_events AS t
USING (
  SELECT
    namespace,
    report_date,
    source,
    source_id,
    source_seq,
    COALESCE(NULLIF(payload.event_id, ''), source_id) AS event_id,
    COALESCE(NULLIF(payload.eventType, ''), NULLIF(payload.event_type, ''), 'unknown') AS event_type,
    COALESCE(
      NULLIF(payload.ownerId, ''),
      NULLIF(payload.editedBy, ''),
      NULLIF(payload.authorId, ''),
      NULLIF(payload.deletedBy, ''),
      NULLIF(payload.userId, ''),
      'unknown'
    ) AS resolved_user_id,
    CASE
      WHEN NULLIF(payload.ownerId, '') IS NOT NULL THEN 'ownerId'
      WHEN NULLIF(payload.editedBy, '') IS NOT NULL THEN 'editedBy'
      WHEN NULLIF(payload.authorId, '') IS NOT NULL THEN 'authorId'
      WHEN NULLIF(payload.deletedBy, '') IS NOT NULL THEN 'deletedBy'
      WHEN NULLIF(payload.userId, '') IS NOT NULL THEN 'userId'
    END AS resolved_user_field,
    COALESCE(
      NULLIF(REGEXP_EXTRACT(COALESCE(payload.timestamp, ''), '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ]([0-9]{2}):[0-9]{2}', 1), ''),
      '00'
    ) AS event_hour,
    payload.timestamp AS event_timestamp,
    NULLIF(payload.documentId, '') AS document_id,
    NULLIF(payload.fileId, '') AS file_id,
    payload.sizeBytes AS size_bytes,
    payload.title AS title,
    payload.name AS name,
    current_timestamp() AS processed_at
  FROM (
    SELECT
      namespace,
      report_date,
      source,
      source_id,
      source_seq,
      source_event_date,
      from_json(
        raw_body,
        'STRUCT<event_id: STRING, eventType: STRING, event_type: STRING, timestamp: STRING, event_date: STRING, ownerId: STRING, editedBy: STRING, authorId: STRING, deletedBy: STRING, userId: STRING, documentId: STRING, fileId: STRING, sizeBytes: BIGINT, title: STRING, name: STRING, _corrupt_record: STRING>',
        map('columnNameOfCorruptRecord', '_corrupt_record')
      ) AS payload
    FROM ow_tp.bronze.cronbox_events_raw
    WHERE namespace = :ns
      AND report_date = :ds
      AND decode_error IS NULL
      AND raw_body IS NOT NULL
  )
  WHERE payload IS NOT NULL
    AND payload._corrupt_record IS NULL
    AND (
      source = 'sqs'
      OR SUBSTRING(COALESCE(source_event_date, payload.event_date, ''), 1, 10) = :ds
    )
) AS s
ON t.namespace = s.namespace
  AND t.report_date = s.report_date
  AND t.source = s.source
  AND t.source_id = s.source_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

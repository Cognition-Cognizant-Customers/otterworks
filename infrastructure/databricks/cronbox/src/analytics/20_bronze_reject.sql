-- cron-analytics bronze rejects: the legacy job swallowed unparseable SQS bodies
-- in a bare `except: pass`. Here every one of them is attributed with a reason.
--
-- Malformed bodies are detected through the corrupt-record column rather than a
-- NULL parse result: from_json runs in PERMISSIVE mode, so a body Jackson cannot
-- parse yields a struct whose declared fields are all NULL, not a NULL struct.
-- Naming a _corrupt_record field makes the failure explicit, and it also catches
-- JSON that parses but is not an object (a bare scalar or array), which the
-- legacy job swallowed the same way when it called .get() on a non-dict.
-- A body that is empty or whitespace parses to a NULL struct instead, so that
-- case is tested separately. This predicate is the exact complement of the
-- acceptance predicate in 30_silver.sql: no row is both rejected and aggregated.
MERGE INTO ow_tp.bronze.cronbox_events_rejected AS t
USING (
  SELECT
    namespace,
    report_date,
    source,
    source_id,
    raw_body,
    raw_body_b64,
    CASE
      WHEN decode_error IS NOT NULL THEN 'invalid_utf8_body'
      ELSE 'unparseable_json_body'
    END AS reason,
    current_timestamp() AS rejected_at
  FROM (
    SELECT
      namespace,
      report_date,
      source,
      source_id,
      raw_body,
      raw_body_b64,
      decode_error,
      from_json(
        raw_body,
        'STRUCT<event_id: STRING, eventType: STRING, event_type: STRING, timestamp: STRING, event_date: STRING, ownerId: STRING, editedBy: STRING, authorId: STRING, deletedBy: STRING, userId: STRING, documentId: STRING, fileId: STRING, sizeBytes: BIGINT, title: STRING, name: STRING, _corrupt_record: STRING>',
        map('columnNameOfCorruptRecord', '_corrupt_record')
      ) AS payload
    FROM ow_tp.bronze.cronbox_events_raw
    WHERE namespace = :ns
      AND report_date = :ds
  )
  WHERE decode_error IS NOT NULL
    OR raw_body IS NULL
    OR payload IS NULL
    OR payload._corrupt_record IS NOT NULL
) AS s
ON t.namespace = s.namespace
  AND t.report_date = s.report_date
  AND t.source = s.source
  AND t.source_id = s.source_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

-- cron-analytics bronze rejects: the legacy job swallowed unparseable SQS bodies
-- in a bare `except: pass`. Here every one of them is attributed with a reason.
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
  FROM ow_tp.bronze.cronbox_events_raw
  WHERE namespace = :ns
    AND report_date = :ds
    AND (
      decode_error IS NOT NULL
      OR raw_body IS NULL
      OR from_json(
           raw_body,
           'STRUCT<event_id: STRING, eventType: STRING, event_type: STRING, timestamp: STRING, event_date: STRING, ownerId: STRING, editedBy: STRING, authorId: STRING, deletedBy: STRING, userId: STRING, documentId: STRING, fileId: STRING, sizeBytes: BIGINT, title: STRING, name: STRING>'
         ) IS NULL
    )
) AS s
ON t.namespace = s.namespace
  AND t.report_date = s.report_date
  AND t.source = s.source
  AND t.source_id = s.source_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

-- cron-analytics bronze: land the extracted batch into Delta, bytes untouched.
-- The landing envelope always carries a well-formed JSON wrapper, so an
-- unparseable payload survives as raw_body text instead of failing the read.
MERGE INTO ow_tp.bronze.cronbox_events_raw AS t
USING (
  SELECT
    namespace,
    CAST(report_date AS DATE) AS report_date,
    source,
    source_id,
    source_seq,
    source_event_date,
    raw_body,
    raw_body_b64,
    decode_error,
    landed_file,
    current_timestamp() AS ingested_at
  FROM (
    SELECT
      namespace,
      report_date,
      source,
      source_id,
      source_seq,
      source_event_date,
      raw_body,
      raw_body_b64,
      decode_error,
      _metadata.file_path AS landed_file,
      _metadata.file_modification_time AS landed_at
    FROM read_files(
      '/Volumes/ow_tp/bronze/landing/cronbox/analytics',
      format => 'json',
      recursiveFileLookup => true,
      schemaHints => 'namespace STRING, report_date STRING, source STRING, source_id STRING, source_seq BIGINT, source_event_date STRING, raw_body STRING, raw_body_b64 STRING, decode_error STRING'
    )
    WHERE namespace = :ns AND report_date = :ds
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY namespace, report_date, source, source_id
      ORDER BY landed_at DESC, landed_file DESC
    ) = 1
  )
) AS s
ON t.namespace = s.namespace
  AND t.report_date = s.report_date
  AND t.source = s.source
  AND t.source_id = s.source_id
WHEN MATCHED THEN UPDATE SET *
WHEN NOT MATCHED THEN INSERT *;

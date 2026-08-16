-- Staging table for the `analytics_daily` extract when the landing volume cannot be written.
--
-- The job's own extract reads `/Volumes/${catalog}/bronze/landing/<ns>/analytics_daily/events/`
-- (see databricks/ddl/analytics_daily.sql and the job definition). Writing there needs a
-- workspace token with the `files` scope; where the caller does not have one, the runner
-- stages the same source lines here over the SQL warehouse and points the extract at this
-- table instead. One row per source line, verbatim, with the source object it came from, so
-- the load stays auditable per legacy S3 object.

CREATE TABLE IF NOT EXISTS ${catalog}.bronze.analytics_daily_stage (
  ns            STRING    COMMENT 'demo namespace this staged slice belongs to',
  source_object STRING    COMMENT 'legacy S3 key the line came from, e.g. 2026/07/29/00.json.gz',
  raw_line      STRING    COMMENT 'one source event record, verbatim',
  staged_at     TIMESTAMP COMMENT 'staging time of this slice'
)
USING DELTA
COMMENT 'Transport-only staging for the analytics_daily extract. Not a target table: it holds the source lines byte-for-byte and is replaced per ns slice.';

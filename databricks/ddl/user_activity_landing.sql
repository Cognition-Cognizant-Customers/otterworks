-- Ingest-edge landing objects for the user-activity conversion.
--
-- 1. user_activity_upstream_fixture: stand-in for the UPSTREAM unit's aggregate,
--    used only to run and reconcile this conversion before the analytics unit
--    lands its own table.
-- 2. user_activity_events_landed: the raw seeded events. The pipeline reads the
--    landing volume by default (source_mode=volume); this table is the fallback
--    ingest target for a workspace token without the `files` scope, which cannot
--    write /Volumes. Either way the pipeline read is one set-based scan.
--
-- Both are unit-prefixed and namespaced: this unit never creates or writes
-- another unit's tables. Table names are catalog-relative for the same reason as
-- databricks/ddl/user_activity_tables.sql.
--
-- The legacy 05:00 job read PostgreSQL `analytics_daily_summary`, produced by the
-- 02:00 `analytics_daily.py` cron. That cron cannot run on a workshop VM (it wants
-- production SQS/DynamoDB), so scripts/tp_databricks/fixture_analytics_upstream.py
-- rebuilds its two artifacts deterministically from the seeded S3 event objects, and
-- scripts/tp_databricks/land_user_activity.py copies the rows into this table.
--
-- In production the job points at the analytics unit's own table via the Terraform
-- variable `user_activity_upstream_table`; this fixture is only what the recon run
-- uses today, and swapping that parameter is the whole migration step.

CREATE TABLE IF NOT EXISTS bronze.user_activity_upstream_fixture (
  ns                STRING COMMENT 'demo namespace',
  report_date       DATE   COMMENT 'date the upstream aggregate covers',
  active_users      BIGINT COMMENT 'distinct users with activity that date',
  active_documents  BIGINT COMMENT 'distinct documents touched',
  active_files      BIGINT COMMENT 'distinct files touched',
  total_events      BIGINT COMMENT 'all events that date; the cross-foot control total',
  documents_created BIGINT,
  documents_edited  BIGINT,
  comments_added    BIGINT,
  files_uploaded    BIGINT,
  files_shared      BIGINT,
  files_deleted     BIGINT,
  bytes_uploaded    BIGINT,
  loaded_at         TIMESTAMP COMMENT 'load time of the fixture row'
)
USING DELTA
PARTITIONED BY (ns)
COMMENT 'Fixture copy of the legacy analytics_daily_summary rows the user-activity job consumes; replaced by the analytics unit target table when that unit lands.';

CREATE TABLE IF NOT EXISTS bronze.user_activity_events_landed (
  ns          STRING    COMMENT 'demo namespace',
  event_id    STRING    COMMENT 'seeded event id',
  event_type  STRING    COMMENT 'document.* | file.* | user.* | quota.*',
  user_id     STRING    COMMENT 'actor',
  resource_id STRING    COMMENT 'document or file the event refers to',
  occurred_at TIMESTAMP COMMENT 'event time',
  loaded_at   TIMESTAMP COMMENT 'load time'
)
USING DELTA
PARTITIONED BY (ns)
COMMENT 'Landed copy of the legacy per-user S3 event objects, read set-based by the pipeline instead of one S3 GET per user per day.';

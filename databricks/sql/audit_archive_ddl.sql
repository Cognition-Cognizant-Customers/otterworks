-- Target tables for the converted audit-archive job (legacy:
-- etl/scripts/audit_archive_weekly.py). Idempotent: re-running this file is a
-- no-op on an existing estate, so it is safe as the job's first task.
--
-- Catalog/schema names are fixed by the migration contract (ow_tp.bronze /
-- silver / gold), so they are literal here rather than parameterised: a SQL
-- file task cannot substitute identifiers.

-- Source audit/metadata events. Replaces the DynamoDB table the legacy job
-- scanned in full to find expired events, and clustering on (ns, event_ts) is what
-- lets the retention predicate be pushed down instead of scanning everything.
CREATE TABLE IF NOT EXISTS ow_tp.bronze.audit_events_raw (
  ns          STRING    NOT NULL COMMENT 'demo namespace, one slice of state per run',
  event_id    STRING    NOT NULL COMMENT 'unique audit event id (legacy DynamoDB event_id)',
  event_ts    TIMESTAMP NOT NULL COMMENT 'when the audited action happened, the retention predicate',
  actor       STRING             COMMENT 'user that performed the action',
  action      STRING             COMMENT 'event type, e.g. file.uploaded',
  target_id   STRING             COMMENT 'resource the action targeted',
  raw_payload STRING             COMMENT 'verbatim source record, kept for replay',
  ingested_at TIMESTAMP          COMMENT 'when the event landed in bronze'
)
CLUSTER BY (ns, event_ts)
COMMENT 'Audit events as landed. Rows are purged only after the archive copy is verified.';

-- Durable archive: the JSONL.gz-on-Glacier copy the legacy job wrote, as a
-- queryable table. retention_days records the policy each row was archived
-- under instead of leaving it implicit in the script.
CREATE TABLE IF NOT EXISTS ow_tp.silver.audit_events_archived (
  ns             STRING    NOT NULL COMMENT 'demo namespace',
  event_id       STRING    NOT NULL COMMENT 'unique audit event id, one row per event_id per ns',
  event_ts       TIMESTAMP NOT NULL COMMENT 'when the audited action happened',
  actor          STRING             COMMENT 'user that performed the action',
  action         STRING             COMMENT 'event type',
  target_id      STRING             COMMENT 'resource the action targeted',
  raw_payload    STRING             COMMENT 'verbatim source record',
  archived_at    TIMESTAMP NOT NULL COMMENT 'when this row was archived',
  retention_days INT       NOT NULL COMMENT 'retention policy this row was archived under',
  cutoff_ts      TIMESTAMP NOT NULL COMMENT 'exclusive retention horizon: event_ts < cutoff_ts',
  run_date       DATE      NOT NULL COMMENT 'execution date of the archiving run'
)
CLUSTER BY (ns, run_date)
COMMENT 'Archived audit events, one row per event_id: the verified durable copy.';

-- Per-run compliance manifest, replacing the report.json the legacy job wrote
-- to S3 with numbers nothing checked.
CREATE TABLE IF NOT EXISTS ow_tp.gold.audit_archive_manifest (
  ns              STRING    NOT NULL COMMENT 'demo namespace',
  run_date        DATE      NOT NULL COMMENT 'execution date, one row per (ns, run_date)',
  cutoff_ts       TIMESTAMP NOT NULL COMMENT 'exclusive retention horizon used by the run',
  candidate_count BIGINT    NOT NULL COMMENT 'events past the horizon: archived + still in bronze',
  archived_count  BIGINT    NOT NULL COMMENT 'events durably archived in silver for this cutoff',
  deleted_count   BIGINT    NOT NULL COMMENT 'archived events purged from bronze, requires verified',
  verified        BOOLEAN   NOT NULL COMMENT 'every candidate is readable from the archive table',
  retention_days  INT       NOT NULL COMMENT 'retention policy in force for the run',
  generated_at    TIMESTAMP NOT NULL COMMENT 'when this manifest row was last computed'
)
COMMENT 'Retention manifest: deleted_count > 0 is only legal when verified is true.';

-- The ordering the legacy job lacked is enforced by the table rather than by
-- reviewer discipline: nothing can be recorded as purged from the source unless
-- the archive copy was verified first, i.e.
--
--     CONSTRAINT deleted_requires_verified CHECK (deleted_count = 0 OR verified)
--
-- It is deliberately NOT managed here. Delta only accepts a CHECK constraint via
-- ALTER TABLE ADD CONSTRAINT, which has no IF NOT EXISTS form and fails on an
-- estate that already carries it, so a SQL file with no control flow can only
-- stay re-runnable by dropping the constraint first -- leaving the manifest
-- unprotected between the two statements, permanently so if the re-add fails.
-- Instead the pipeline (databricks/notebooks/ow_tp_audit_archive.py) adds the
-- constraint when `delta.constraints.deleted_requires_verified` is absent and
-- never drops it, which runs before any manifest row is written and cannot leave
-- a window where a purge could be recorded unverified.

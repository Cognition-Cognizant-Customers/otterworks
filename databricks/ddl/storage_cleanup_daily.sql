-- Unity Catalog tables for ow_tp_storage_cleanup (converted from
-- etl/scripts/storage_cleanup_daily.py). Idempotent: the job runs this file
-- before every pipeline run, and demo state is per-namespace (`ns`), so a
-- rerun replaces its own slice instead of duplicating it.
--
-- Statements are separated by a line containing only `-- @statement`; the
-- driver (scripts/tp_databricks/dbx.py) and the job notebook both split on it,
-- because the SQL Statement Execution API takes one statement per call.

CREATE TABLE IF NOT EXISTS ow_tp.bronze.storage_objects_raw (
  ns            STRING    COMMENT 'demo namespace this row belongs to',
  bucket        STRING    COMMENT 'source bucket the object was listed from',
  key           STRING    COMMENT 'object key, as listed',
  size_bytes    BIGINT,
  last_modified TIMESTAMP,
  listed_at     TIMESTAMP COMMENT 'when the inventory extract ran'
)
COMMENT 'Object inventory replacing the legacy per-object list_objects_v2 walk.';

-- @statement

CREATE TABLE IF NOT EXISTS ow_tp.bronze.file_metadata_raw (
  ns          STRING,
  file_id     STRING,
  storage_key STRING COMMENT 'DynamoDB s3_key attribute: the object this item claims',
  owner_id    STRING,
  size_bytes  BIGINT,
  created_at  TIMESTAMP
)
COMMENT 'file-metadata items replacing the legacy item-by-item DynamoDB scan.';

-- @statement

-- Not named in the contract's table list, but the conversion needs one durable
-- fact the legacy script never wrote down: how much of the metadata side the
-- extract actually managed to read. Keeping it as a table (rather than a job
-- parameter) means every orphan verdict can be audited back to the read that
-- produced it. One row per namespace: the extract replaces its own slice.
CREATE TABLE IF NOT EXISTS ow_tp.bronze.storage_extract_manifest (
  ns                     STRING,
  scenario               STRING  COMMENT 'nominal | metadata_read_incomplete',
  source_bucket          STRING,
  source_table           STRING,
  objects_expected       BIGINT  COMMENT 'objects the extract listed, for load cross-check',
  objects_bytes          BIGINT,
  metadata_expected      BIGINT  COMMENT 'metadata items the extract read',
  metadata_read_complete BOOLEAN COMMENT 'FALSE => the DynamoDB scan did not finish',
  extracted_at           TIMESTAMP,
  loaded_at              TIMESTAMP
)
COMMENT 'Provenance of each extract, including whether the metadata read completed.';

-- @statement

-- An orphan verdict is only valid when the metadata side was read completely:
-- metadata_read_ok carries that fact per row, so a partial read can never be
-- mistaken for "these files have no owner".
CREATE TABLE IF NOT EXISTS ow_tp.silver.storage_orphans (
  ns               STRING,
  bucket           STRING,
  key              STRING,
  size_bytes       BIGINT,
  orphan_reason    STRING  COMMENT 'no_metadata_row (confirmed) or candidate_unverified_metadata_read (not actionable)',
  detected_at      TIMESTAMP,
  metadata_read_ok BOOLEAN COMMENT 'FALSE => candidate only; nothing may be quarantined from this run',
  scenario         STRING  COMMENT 'nominal | metadata_read_incomplete (safety-guard demonstration)'
)
COMMENT 'Objects with no metadata row, as a set difference instead of a per-object probe.';

-- @statement

CREATE TABLE IF NOT EXISTS ow_tp.gold.storage_cleanup_savings (
  ns                STRING,
  run_date          DATE,
  objects_scanned   BIGINT,
  metadata_rows     BIGINT,
  orphan_count      BIGINT,
  orphan_bytes      BIGINT,
  quarantined_count BIGINT COMMENT '0 unless the metadata read was complete AND dry_run is false',
  dry_run           BOOLEAN,
  scenario          STRING,
  metadata_read_ok  BOOLEAN,
  generated_at      TIMESTAMP
)
COMMENT 'Durable savings report replacing the JSON dropped in an S3 prefix nobody reads.';

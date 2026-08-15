-- Tables for ow_tp_search_reindex, the conversion of
-- etl/scripts/search_reindex_weekly.py. Idempotent: safe to re-apply, and
-- re-running the job replaces a namespace's rows rather than appending
-- (the legacy cron had no notion of idempotency).
--
-- Apply with:
--   python3 scripts/tp_databricks/apply_sql.py databricks/sql/search_reindex_tables.sql
--
-- Statements are separated by a line containing only `;`.

CREATE TABLE IF NOT EXISTS ow_tp.bronze.search_documents_raw (
  ns STRING NOT NULL COMMENT 'Demo namespace; every row is scoped so parallel rehearsals never collide.',
  entity_type STRING NOT NULL COMMENT 'document | file -- the legacy indices this row feeds.',
  entity_id STRING NOT NULL COMMENT 'Source primary key: document id or file id.',
  payload STRING NOT NULL COMMENT 'The source API record exactly as extracted, as JSON.',
  extracted_at TIMESTAMP NOT NULL COMMENT 'When the extract that produced this row was taken.'
)
USING DELTA
CLUSTER BY (ns, entity_type)
COMMENT 'Documents and files as extracted from document-service and file-service, unparsed. Replaces the legacy cron reading the paginated APIs straight into MeiliSearch with no durable copy.'
;

CREATE TABLE IF NOT EXISTS ow_tp.silver.search_index_documents (
  ns STRING NOT NULL,
  entity_type STRING NOT NULL COMMENT 'document | file.',
  entity_id STRING NOT NULL COMMENT 'One row per entity: the index primary key.',
  title STRING COMMENT 'Document title; null for files.',
  content STRING COMMENT 'Document body; null for files.',
  name STRING COMMENT 'File name; null for documents.',
  mime_type STRING COMMENT 'File mime type; null for documents.',
  folder_id STRING COMMENT 'File folder; null for documents.',
  size_bytes BIGINT COMMENT 'File size; null for documents.',
  owner_id STRING,
  tags ARRAY<STRING> COMMENT 'Empty array rather than null when the source omits tags, matching what the legacy index stored.',
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  run_date DATE NOT NULL COMMENT 'Run that published this row.',
  indexed_at TIMESTAMP NOT NULL
)
USING DELTA
CLUSTER BY (ns, entity_type)
COMMENT 'The index-ready projection served to search: typed, deduplicated, one row per entity. Published by an atomic replace of the namespace partition, never cleared first.'
;

-- Staging half of the build-then-swap. The job builds here, reconciles counts
-- against bronze, and only then replaces the serving partition. The legacy
-- failure mode (indices deleted, extract dies, search empty) cannot occur:
-- nothing touches the serving table until the counts agree.
CREATE TABLE IF NOT EXISTS ow_tp.silver.search_index_documents_staging (
  ns STRING NOT NULL,
  entity_type STRING NOT NULL,
  entity_id STRING NOT NULL,
  title STRING,
  content STRING,
  name STRING,
  mime_type STRING,
  folder_id STRING,
  size_bytes BIGINT,
  owner_id STRING,
  tags ARRAY<STRING>,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  run_date DATE NOT NULL,
  indexed_at TIMESTAMP NOT NULL
)
USING DELTA
CLUSTER BY (ns, entity_type)
COMMENT 'Build target for ow_tp_search_reindex; contents are transient per (ns, run_date).'
;

CREATE TABLE IF NOT EXISTS ow_tp.gold.search_reindex_summary (
  ns STRING NOT NULL,
  run_date DATE NOT NULL,
  entity_type STRING NOT NULL,
  source_count BIGINT NOT NULL COMMENT 'Entities landed in bronze for this run.',
  indexed_count BIGINT NOT NULL COMMENT 'Rows published to silver.search_index_documents.',
  counts_match BOOLEAN NOT NULL COMMENT 'False fails the run; the legacy script only printed the mismatch and exited zero.',
  swap_completed BOOLEAN NOT NULL COMMENT 'True only after the serving partition was replaced.'
)
USING DELTA
CLUSTER BY (ns, run_date)
COMMENT 'Per-run reindex outcome, one row per entity type. Replaces the legacy print()-only count validation.'
;

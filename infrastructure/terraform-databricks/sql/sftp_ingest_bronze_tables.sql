-- Bronze tables for the converted `sftp_ingest_poll.ksh` (job `ow_tp_sftp_ingest`).
--
-- The legacy job had no state at all beyond files on a filesystem: completeness
-- was guessed from two `wc -c` readings a second apart, and re-ingestion was
-- "prevented" by renaming inputs to `.done` plus a lock file nothing removed.
-- These two tables replace that: `custbill_files` is the checksum manifest the
-- handshake is built on, `custbill_lines` keeps the raw record bytes untouched
-- for the parser unit downstream.
--
-- Run with :catalog bound to the target catalog (the job passes var.catalog_name).
-- Idempotent by construction: CREATE TABLE IF NOT EXISTS, and the ingest merges.

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.bronze.custbill_files') (
  ns            STRING    NOT NULL COMMENT 'Demo namespace, scoping every row to one run namespace.',
  file_name     STRING    NOT NULL COMMENT 'Drop file name as delivered by the mainframe extract (CB77340).',
  size_bytes    BIGINT    NOT NULL COMMENT 'Byte length of the ingested file, measured once from the landed bytes.',
  sha256        STRING    NOT NULL COMMENT 'SHA-256 of the landed bytes - the transfer-completion handshake replacing the size-settle heuristic.',
  record_count  BIGINT    NOT NULL COMMENT 'Record lines ingested from the file, header and trailer included.',
  ingested_at   TIMESTAMP NOT NULL COMMENT 'First successful ingest of this (ns, file_name, sha256), unchanged by re-runs.',
  source_path   STRING    NOT NULL COMMENT 'Full landing-volume path the bytes were read from.'
)
USING DELTA
CLUSTER BY (ns, file_name)
COMMENT 'Bronze manifest - one row per ingested CUSTBILL drop file, keyed (ns, file_name).';

CREATE TABLE IF NOT EXISTS IDENTIFIER(:catalog || '.bronze.custbill_lines') (
  ns        STRING NOT NULL COMMENT 'Demo namespace.',
  file_name STRING NOT NULL COMMENT 'Drop file the line came from.',
  line_no   INT    NOT NULL COMMENT '1-based line number within the file, preserving delivery order.',
  raw_line  STRING NOT NULL COMMENT 'The record exactly as delivered - fixed-width, trailing blanks intact, unparsed.'
)
USING DELTA
CLUSTER BY (ns, file_name)
COMMENT 'Bronze raw records - one row per line of every ingested CUSTBILL drop. Parsing belongs to the next unit.';

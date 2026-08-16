-- Unity Catalog tables for the estate wave (`etl/legacy-extra/run_all.sh` +
-- `etl/legacy-extra/crontab` -> `ow_tp_estate_orchestrator` / `ow_tp_estate_rollup`).
--
-- Idempotent: every statement is CREATE ... IF NOT EXISTS, so the notebook can apply it on
-- every run and a re-run is a no-op. `${catalog}` is substituted by whoever applies it (the
-- notebook task from its `catalog` parameter, the local runner from the driver's catalog),
-- the convention databricks/ddl/analytics_daily.sql established, so one reviewed statement
-- text serves both and no catalog is hardcoded here.
--
-- Both gold tables are per-namespace demo state: `ns` is the isolation boundary, and each
-- load replaces exactly the slice it recomputes (`INSERT ... REPLACE WHERE`), never
-- appending a second copy of a re-run.

CREATE TABLE IF NOT EXISTS ${catalog}.gold.estate_daily_rollup (
  ns               STRING    COMMENT 'demo namespace the rolled-up slice belongs to',
  run_date         DATE      COMMENT 'orchestrator run date this rollup describes; one slice per (ns, run_date)',
  unit             STRING    COMMENT 'converted unit: sftp_ingest | parse_custbill | finance_report | analytics_daily | audit_archive | search_reindex | storage_cleanup | user_activity',
  legacy_source    STRING    COMMENT 'repo path of the legacy script this unit replaced',
  language_vintage STRING    COMMENT 'language and vintage of that legacy script, e.g. ksh (1998)',
  rows_in          BIGINT    COMMENT 'rows the unit read, in the unit of measure named in recon_detail (records, files, or objects)',
  rows_out         BIGINT    COMMENT 'rows the unit published to its silver/gold target',
  rejected         BIGINT    COMMENT 'rows the unit quarantined or could not account for; the legacy estate discarded these silently',
  recon_result     STRING    COMMENT 'green | red | blocked -- derived from the recon evidence the unit persists, never hand-entered. green requires the parity identity in recon_detail to hold; blocked means the source slice is absent, so nothing was reconciled',
  recon_detail     STRING    COMMENT 'the evidence behind recon_result: the parity identity with its numbers, the table columns it was read from, and any disclosure attached to the unit (dry-run, undelivered artifact, stale slice)',
  job_run_id       STRING    COMMENT 'Databricks run id of the ow_tp_estate_rollup run that wrote the row; empty for a local warehouse run, which the recon report labels as such',
  updated_at       TIMESTAMP COMMENT 'write time of this row'
)
USING DELTA
PARTITIONED BY (ns)
COMMENT 'One row per (ns, run_date, unit): whether a night''s batch actually reconciled, across all eight converted units. The legacy estate had no estate-level view at all -- run_all.sh suppressed every stage with 2>/dev/null || true.';

;

CREATE TABLE IF NOT EXISTS ${catalog}.gold.estate_anomalies (
  ns          STRING    COMMENT 'demo namespace the anomaly was found in',
  unit        STRING    COMMENT 'converted unit whose tables surface the anomaly, or seed_manifest when no converted unit ingests the affected source',
  anomaly_type STRING   COMMENT 'seed-manifest anomaly kind: orphaned_metadata | missing_hours | version_gaps | orphaned_snapshots',
  detail      STRING    COMMENT 'the anomalous identifier plus the manifest entry it traces to (kind, target, planted count), so every row is auditable back to testdata/legacy/manifests/<ns>.json',
  detected_at TIMESTAMP COMMENT 'detection time of this row'
)
USING DELTA
PARTITIONED BY (ns)
COMMENT 'Data defects planted by the seed generator and surfaced from the converted tables. The legacy estate surfaced none of them: orphaned metadata looked like an orphaned object, and a missing event hour looked like a quiet hour.';

;

CREATE TABLE IF NOT EXISTS ${catalog}.bronze.seed_anomaly_manifest (
  ns                    STRING    COMMENT 'namespace the manifest was generated for',
  kind                  STRING    COMMENT 'planted anomaly kind, verbatim from the manifest',
  target                STRING    COMMENT 'seeded store the anomalies were planted in, verbatim from the manifest',
  planted_count         BIGINT    COMMENT 'how many anomalies of this kind the generator planted',
  manifest_generated_at STRING    COMMENT 'generated_at field of the manifest, so a rollup can be tied to the seed run it describes',
  manifest_sha256       STRING    COMMENT 'sha256 of the manifest file as landed; the anomaly rows cite it, so a re-seeded namespace is visible rather than silent',
  loaded_at             TIMESTAMP COMMENT 'load time of this manifest slice'
)
USING DELTA
PARTITIONED BY (ns)
COMMENT 'The seed manifest (testdata/legacy/manifests/<ns>.json) as a table, landed over the serverless warehouse because the demo PAT lacks the files scope. It is runtime state, never committed, and it is what makes every gold.estate_anomalies row traceable to a planted count.';

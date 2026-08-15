-- Retention for the converted `sftp_ingest_poll.ksh`.
--
-- The legacy job had none: `archive/` grew forever and inputs were renamed
-- `.done` and never purged. Retention is now declared here and enforced by the
-- `retention` task of `ow_tp_sftp_ingest` on every run.
--
-- Parameters: :catalog, :ns, :retention_days.

DELETE FROM IDENTIFIER(:catalog || '.bronze.custbill_lines')
WHERE ns = :ns
  AND file_name IN (
    SELECT file_name
    FROM IDENTIFIER(:catalog || '.bronze.custbill_files')
    WHERE ns = :ns
      AND ingested_at < current_timestamp() - make_interval(0, 0, 0, CAST(:retention_days AS INT))
  );

DELETE FROM IDENTIFIER(:catalog || '.bronze.custbill_files')
WHERE ns = :ns
  AND ingested_at < current_timestamp() - make_interval(0, 0, 0, CAST(:retention_days AS INT));

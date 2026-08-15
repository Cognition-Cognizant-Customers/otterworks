-- Retention for the converted `sftp_ingest_poll.ksh`.
--
-- The legacy job had none: `archive/` grew forever and inputs were renamed
-- `.done` and never purged. Retention is now declared here and enforced by the
-- `retention` task of `ow_tp_sftp_ingest` on every run.
--
-- Scope: rows only. Landing stays the archive.
--
-- This deliberately does NOT remove the landed drop file, and there is no
-- tombstone of already-processed files. The legacy job renamed each drop to
-- `*.done` in place and kept it forever, and those `.done` artifacts are the
-- golden baseline this conversion reconciles against, so file-as-archive is the
-- semantics that matches the before-state. The consequence is intended: a file
-- whose rows have been trimmed re-ingests on a later run, with landing acting as
-- the replay source. It cannot duplicate — the manifest is keyed on
-- (ns, file_name) and carries the whole-file sha256 — so `ingested_at` reads as
-- "when the current content was last ingested", not "first ever seen".
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

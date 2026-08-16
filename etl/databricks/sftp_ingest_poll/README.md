# sftp_ingest_poll (Databricks migration unit)

Medallion conversion of `etl/legacy-extra/jobs/sftp_ingest_poll.ksh` (ksh, 1998).
Contract: `docs/tech-partnerships/contracts/sftp_ingest_poll.json`.

- `sftp_ingest_poll_notebook.py` — serverless notebook (deployed to
  `/Shared/ow_tp/sftp_ingest_poll` by `infrastructure/terraform-databricks/jobs_sftp_ingest_poll.tf`).
  Scans `/Volumes/ow_tp/bronze/landing/<ns>/sftp_ingest_poll/` and registers one
  row per landed file in `ow_tp.bronze.custbill_raw_files` (ns, file_name,
  byte_count, sha256, landed_at). Byte-transparent: files are hashed as opaque
  bytes, never decoded.
- `recon_sftp_ingest_poll.py` — fixture recon (run from repo root); writes
  `docs/tech-partnerships/recon/sftp_ingest_poll.recon.json` with
  `run_mode=fixture`. Live proof is parent-owned.

Retired legacy deficiencies: lock-file poison, size-compared-twice settle
heuristic (atomic Files API PUT transport), hostname if-blocks (all paths
derive from `ns` + `volume_root` parameters).

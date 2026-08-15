# Contract: `sftp_ingest_poll.ksh` → `ow_tp_sftp_ingest`

Read [README.md](README.md) first — the shared rules there are part of this contract.

| | |
|---|---|
| Source | `etl/legacy-extra/jobs/sftp_ingest_poll.ksh` |
| Language / vintage | ksh, 1998 (ported 2014) |
| Legacy schedule | every 15 min (crontab `*/15`) |
| Converted job | `ow_tp_sftp_ingest` (`infrastructure/terraform-databricks/jobs_sftp_ingest.tf`) |
| Position | bronze ingest — first stage of the CUSTBILL chain |

## Deficiencies this conversion must retire

From `etl/legacy-extra/ETL_UPGRADE_GUIDE_ADDENDUM.md`:

- Hardcoded per-environment absolute paths (`/data/otterworks`, `/data2/otterworks_uat`)
  selected by hostname if-blocks → externalized config, no hostname branching.
- Lock file checked but never removed; a crashed run poisons every later run → idempotent,
  lock-free design (or real mutual exclusion via `max_concurrent_runs = 1`).
- No transfer-completion protocol: "settles" by comparing file size twice, 1s apart →
  checksum/manifest handshake, so a half-written file can never be ingested.
- Blanket error suppression (`2>/dev/null || true`) → errors surfaced and the run fails.
- No retention policy: `archive/` grows forever, inputs renamed `.done` and never purged →
  explicit retention expressed in the target.
- Credentials inline in the script → secret scope `ow_tp`.

## Target

| Object | Contents |
|---|---|
| `/Volumes/ow_tp/bronze/landing/demo/custbill/` | the raw fixed-width drops, uploaded by `make dbx-upload NS=demo` |
| `ow_tp.bronze.custbill_files` | one row per ingested file: `ns`, `file_name`, `size_bytes`, `sha256`, `record_count`, `ingested_at`, `source_path`. This is the manifest that replaces the size-settle heuristic. |
| `ow_tp.bronze.custbill_lines` | one row per raw record line, untyped: `ns`, `file_name`, `line_no`, `raw_line`. Parsing is the next unit's job; bronze stays faithful to the bytes. |

Idempotency requirement: re-running `ow_tp_sftp_ingest` for the same `ns` must leave both
tables byte-identical (dedupe on `(ns, file_name, line_no)` / `(ns, file_name, sha256)`),
which is the property the legacy `.done`-rename + lock-file scheme fails to provide.

## Golden legacy output

Captured on this VM at `/home/ubuntu/tp-golden/custbill/` (see `MANIFEST.md` there):

| Golden artifact | Bytes | Lines | SHA-256 |
|---|---:|---:|---|
| `incoming/CUSTBILL_DEMO_001.dat.done` | 3430 | 52 | `c70f30ca08842885fe2bc96c3902d463609d05be95a96c01875b825a88aa336c` |
| `incoming/CUSTBILL_DEMO_002.dat.done` | 3430 | 52 | `652974b8bb3a168483c8f63fb9f2db440a6ff606f4563cd0800f15914b344f48` |
| `archive/CUSTBILL_DEMO_001.dat.<ts>` | 3430 | 52 | same as above |
| `archive/CUSTBILL_DEMO_002.dat.<ts>` | 3430 | 52 | same as above |

Regenerate with `make legacy-etl-gen-data NS=demo` then
`make legacy-etl-run JOB=sftp_ingest_poll` (root `/tmp/otterworks-legacy`). Content is
deterministic for `NS=demo`; only the archive filename timestamp varies.

## Acceptance checks (`scripts/tp_databricks/recon_sftp_ingest.py`)

1. `bronze.custbill_files` has exactly 2 rows for `ns='demo'`, file names matching the
   golden set, `size_bytes = 3430` each, and `sha256` equal to the golden hashes above —
   hash equality against the legacy artifact, not a recomputation of the uploaded copy.
2. `bronze.custbill_lines` has 52 rows per file, 104 total for `ns='demo'`; each
   `raw_line` length matches the legacy record length and the header/trailer records are
   present and unaltered.
3. The trailer record count declared in each file equals the number of detail lines
   ingested for that file (the reconciliation the legacy chain logs but never checks).
4. Re-run the job and assert both tables are unchanged (idempotency).
5. Assert no `ow_tp` object outside the three targets above was created, and no unprefixed
   object was touched.

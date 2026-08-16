# Contract: `parse_custbill_fixedwidth.sh` → `ow_tp_parse_custbill`

Read [README.md](README.md) first — the shared rules there are part of this contract.

| | |
|---|---|
| Source | `etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh` |
| Language / vintage | bash + `sed`/`awk`/`cut`, 2001 (copybook CBCUST01) |
| Legacy schedule | every 15 min, offset `:05` |
| Converted job | `ow_tp_parse_custbill` (`infrastructure/terraform-databricks/jobs_parse_custbill.tf`) |
| Position | silver — depends on the bronze tables from `ow_tp_sftp_ingest` |

## Deficiencies this conversion must retire

- Fixed-width parsing via three passes of `cut` + `sed` + `awk`, no validation, bad records
  pass straight through → schema-validated parse with typed columns.
- Trailer record count logged but never reconciled → reconciliation enforced, run fails on
  mismatch.
- Implied-decimal amounts and dates handled by string surgery with no validity checks
  (invalid dates pass through reformatted) → typed `DECIMAL(18,2)` / `DATE`, with invalid
  records quarantined rather than silently emitted.
- Temp files with PID suffixes (`/tmp/cb_body.$$`) left behind on failure → no orphaned
  state.
- Cron-offset coupling to the ingest job (`:00`/`:15` vs `:05`) can read a half-written
  file → explicit task dependency on the ingest job, `max_concurrent_runs = 1`.
- Blanket `2>/dev/null || true`; lock file never removed; hostname-selected paths → as in
  the ingest contract.

## Target

| Object | Contents |
|---|---|
| `ow_tp.silver.custbill_records` | typed detail records: `ns`, `file_name`, `line_no`, `record_type` (`01`/`02`), `account_id`, `invoice_id`, `currency`, `amount DECIMAL(18,2)`, `bill_date DATE`, `parsed_at`. `amount` carries the implied decimal applied numerically, not by string insertion. |
| `ow_tp.silver.custbill_rejects` | records failing schema/validity checks: `ns`, `file_name`, `line_no`, `raw_line`, `reject_reason`. Must be present even when empty — a visible quarantine is the point. |
| `ow_tp.silver.custbill_file_recon` | per file: `declared_trailer_count`, `parsed_count`, `rejected_count`, `recon_ok BOOLEAN`. |

## Golden legacy output

`/home/ubuntu/tp-golden/custbill/parsed/` (see `MANIFEST.md`):

| Golden artifact | Bytes | Data lines | SHA-256 |
|---|---:|---:|---|
| `CUSTBILL_DEMO_001.psv` | 2484 | 50 | `7fc03e8ceb88ce807b18e3e0a8bb2450b7677108495bdcb883881887c09665bf` |
| `CUSTBILL_DEMO_002.psv` | 2468 | 50 | `b576ad3de53b835643dc9096781cb491e6a03b3712c675c5598ab05f8c3c54a3` |

100 parsed rows total for `NS=demo`. Per-file legacy subtotals (record type / currency /
count / amount):

- `CUSTBILL_DEMO_001.psv` — `01`: EUR 12 / 55683.32, GBP 16 / 107084.75, USD 15 / 70039.36;
  `02`: EUR 2 / 12243.83, GBP 2 / 9116.73, USD 3 / 21160.45
- `CUSTBILL_DEMO_002.psv` — `01`: EUR 10 / 45871.09, GBP 16 / 76028.83, USD 13 / 60462.79;
  `02`: EUR 4 / 21132.14, GBP 3 / 19337.86, USD 4 / 12229.99

Regenerate with `make legacy-etl-gen-data NS=demo` then `make legacy-etl-run
JOB=sftp_ingest_poll` and `make legacy-etl-run JOB=parse_custbill_fixedwidth`.

## Acceptance checks (`scripts/tp_databricks/recon_parse_custbill.py`)

1. Row-level parity: parse the golden `.psv` files and compare **every** field of every
   row against `silver.custbill_records` for `ns='demo'` — 100 rows, no extras, no
   missing, keyed on `(file_name, line_no)`. Amounts compared as exact decimals (to the
   cent), dates as dates.
2. Per-file subtotals equal the table above exactly, per record type and currency.
3. `silver.custbill_file_recon` shows `declared_trailer_count = parsed_count + rejected_count`
   and `recon_ok = true` for both files.
4. Any row the converted job quarantines must be justified in the report: if the legacy
   output contains a row the conversion rejects, that is a real difference and recon is
   **not** green — report it rather than dropping it from the comparison.
5. Re-run the job; assert row counts and totals unchanged (idempotency).

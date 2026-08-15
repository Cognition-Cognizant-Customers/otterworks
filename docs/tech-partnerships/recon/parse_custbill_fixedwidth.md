# Recon: `parse_custbill_fixedwidth.sh` -> `ow_tp_parse_custbill`

- Namespace: `ns=demo`
- Golden legacy output: `/home/ubuntu/tp-golden/custbill/parsed` (regenerated with `make legacy-etl-gen-data NS=demo`, `make legacy-etl-run JOB=sftp_ingest_poll`, `make legacy-etl-run JOB=parse_custbill_fixedwidth`)
- Converted output: `ow_tp.silver.custbill_records` / `ow_tp.silver.custbill_rejects` / `ow_tp.silver.custbill_file_recon`

## Unverified: landing-volume upload path

The upload path into `/Volumes/ow_tp/bronze/landing` is UNVERIFIED. The demo PAT lacks the
`files` scope, so every upload attempt fails with `Provided access token does not have
required scopes: files`. The bronze source reconciled here is the `ow_tp_sftp_ingest` unit's
`bronze.custbill_files` / `bronze.custbill_lines`. No check was weakened or skipped to
compensate; this statement covers only the upload leg, which this unit does not exercise.
- Reproduce: `NS=demo python3 scripts/tp_databricks/recon_parse_custbill.py`
- Negative controls (quarantine and trailer gate actually failing a run): [parse_custbill_negative_controls.md](parse_custbill_negative_controls.md)

**Result: green**

| Check | Result |
|---|---|
| 0. Golden baseline reproduced locally (bytes / data lines / SHA-256) | **PASS** |
| 1. Row-level parity: every field of every row, keyed on (file, line_no) | **PASS** |
| 2. Per-file subtotals per record type and currency, exact to the cent | **PASS** |
| 3. Trailer reconciliation: declared_trailer_count = parsed + rejected, recon_ok | **PASS** |
| 4. Quarantine justified: nothing the legacy output contains is rejected | **PASS** |
| 5. Idempotency: re-running the job leaves counts and totals unchanged | **PASS** |

## 0. Golden baseline reproduced locally (bytes / data lines / SHA-256) — PASS

- CUSTBILL_DEMO_001.psv: 2484 bytes / 50 lines / 7fc03e8ceb88ce807b18e3e0a8bb2450b7677108495bdcb883881887c09665bf — matches contract
- CUSTBILL_DEMO_002.psv: 2468 bytes / 50 lines / b576ad3de53b835643dc9096781cb491e6a03b3712c675c5598ab05f8c3c54a3 — matches contract

## 1. Row-level parity: every field of every row, keyed on (file, line_no) — PASS

- golden rows: 100; converted rows: 100
- all 100 rows match on all 6 fields

## 2. Per-file subtotals per record type and currency, exact to the cent — PASS

- CUSTBILL_DEMO_001 01 EUR: 12 / 55683.32
- CUSTBILL_DEMO_001 01 GBP: 16 / 107084.75
- CUSTBILL_DEMO_001 01 USD: 15 / 70039.36
- CUSTBILL_DEMO_001 02 EUR: 2 / 12243.83
- CUSTBILL_DEMO_001 02 GBP: 2 / 9116.73
- CUSTBILL_DEMO_001 02 USD: 3 / 21160.45
- CUSTBILL_DEMO_002 01 EUR: 10 / 45871.09
- CUSTBILL_DEMO_002 01 GBP: 16 / 76028.83
- CUSTBILL_DEMO_002 01 USD: 13 / 60462.79
- CUSTBILL_DEMO_002 02 EUR: 4 / 21132.14
- CUSTBILL_DEMO_002 02 GBP: 3 / 19337.86
- CUSTBILL_DEMO_002 02 USD: 4 / 12229.99

## 3. Trailer reconciliation: declared_trailer_count = parsed + rejected, recon_ok — PASS

- CUSTBILL_DEMO_001.dat: declared 50 = parsed 50 + rejected 0, recon_ok=true
- CUSTBILL_DEMO_002.dat: declared 50 = parsed 50 + rejected 0, recon_ok=true

## 4. Quarantine justified: nothing the legacy output contains is rejected — PASS

- silver.custbill_rejects exists (present even when empty)
- quarantined rows for ns=demo: 0

## 5. Idempotency: re-running the job leaves counts and totals unchanged — PASS

- before re-run: 100 rows, total 510391.14
- after re-run: 100 rows, total 510391.14
- every row identical field-by-field across the re-run


# Incident: CUSTBILL history reconciliation failure (ns `rehearsalplat`)

- **Date:** 2026-08-17
- **Detected by:** Databricks recon job 613726149349528, failed run [373323304874944](https://dbc-8bc9474f-40ae.cloud.databricks.com/jobs/613726149349528/runs/373323304874944)
- **Failing checks:** `file_count/2024` (expected 12, actual 13), `quarantine_count/2024` (expected 4, actual 9)

## Root cause

A malformed batch `CUSTBILL_DRIFT_202406.dat` (7 lines: HDR + 5 body records + TRL)
landed in the volume at
`/Volumes/ow_tp/bronze/landing/rehearsalplat/history/2024/` and was ingested into
`ow_tp.bronze.custbill_history_raw_rehearsalplat` for period 202406. All 5 body
records carried a non-numeric amount (`0000000ABCDE`) and were correctly routed to
`ow_tp.silver.custbill_quarantine_rehearsalplat` with reason `nonnumeric_amount`.
This is not part of the legacy history (the legacy-derived expectations manifest
covers exactly 72 files / 2880 records / 30 planted anomalies), so:

- 2024 file count: 13 vs expected 12
- 2024 quarantine count: 9 vs expected 4 (5 extra `nonnumeric_amount` rows)
- gold annual totals were unaffected (every malformed record was quarantined),
  confirmed by Delta time travel: totals AS OF v3 and v4 of
  `ow_tp.gold.custbill_annual_rehearsalplat` were both
  `record_count=2856, total_amount_cents=1462065686`.

## Evidence (before fix)

Bronze 2024 files: 12x `CUSTBILL_REHEARSALPLAT_2024MM.dat` (42 rows each) plus
`CUSTBILL_DRIFT_202406.dat` (7 rows). Quarantine 2024 by reason:

| source_file | reason | count |
|---|---|---|
| CUSTBILL_DRIFT_202406.dat | nonnumeric_amount | 5 |
| CUSTBILL_REHEARSALPLAT_202403.dat | invalid_calendar_date | 1 |
| CUSTBILL_REHEARSALPLAT_202405.dat | nonnumeric_amount | 1 |
| CUSTBILL_REHEARSALPLAT_202409.dat | invalid_calendar_date | 1 |
| CUSTBILL_REHEARSALPLAT_202411.dat | nonnumeric_amount | 1 |
| CUSTBILL_REHEARSALPLAT_202412.dat | trailer_count_mismatch | 1 |

Recon (before): `checks: 49, failed: 2, anomalies expected/actual: 30/35, missing: 0, unexpected: 5`.

## Remediation

Smallest correct action — no pipeline code was wrong, no target table was hand-edited:

1. Deleted the malformed batch from the landing volume (Files API, HTTP 204):
   `/Volumes/ow_tp/bronze/landing/rehearsalplat/history/2024/CUSTBILL_DRIFT_202406.dat`
2. Re-ran the standard incremental backfill for the affected period:
   `python3 scripts/tp_dbx/showcase.py --ns rehearsalplat backfill --period 202406`
   (deletes bronze period 202406 and reloads it from the landing glob, then rebuilds
   silver, quarantine and gold). Post-backfill: 72 files, 3024 bronze rows,
   2856 silver, 30 quarantined.

## Proof (after fix)

- Recon green: `checks: 49, failed: 0, anomalies expected/actual: 30/30, missing: 0, unexpected: 0`
  — report committed at `docs/tech-partnerships/recon/custbill_history_backfill-rehearsalplat.recon.json`
  (`run_mode: live`, idempotency rerun `pass`: "silver/quarantine/gold rebuilt from bronze; all 49 checks byte-identical").
- Delta time travel after fix: gold at v5/v6 still `record_count=2856, total_amount_cents=1462065686`
  (v5 = rebuild during backfill, v6 = idempotency rerun; byte-identical totals).
- Recon job re-triggered and green:
  [run 866479756184737](https://dbc-8bc9474f-40ae.cloud.databricks.com/jobs/runs/866479756184737)
  — `result: SUCCESS`, task `recon_check: SUCCESS`, `notify_devin: EXCLUDED`.
- Job schedule remains **PAUSED**.

No repository code changed: the pipeline handled the malformed batch correctly
(quarantined every bad record); the failure was recon correctly flagging a
non-legacy file in the target. This note plus the committed recon report are the
audit artifact.

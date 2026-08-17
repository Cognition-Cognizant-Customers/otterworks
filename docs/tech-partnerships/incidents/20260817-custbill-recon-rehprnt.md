# Incident: CUSTBILL history reconciliation failure (ns=rehprnt)

- **Detected by:** Databricks recon job `ow_tp_billing_history_recon_rehprnt`
  (job 518698192572610), failed run:
  https://dbc-8bc9474f-40ae.cloud.databricks.com/jobs/518698192572610/runs/9257219846441
- **Date:** 2026-08-17 (UTC)
- **Namespace:** `rehprnt` · catalog `ow_tp` · base branch `tp-run/databricks-20260817T043248Z`

## Failing checks (before)

9 checks failed, all pointing at the 2025 slice being absent from the target:

| check_id | expected | actual |
|---|---|---|
| annual_total/2025/EUR/01 | 124\|60821750 | 0\|0 |
| annual_total/2025/EUR/02 | 27\|14738929 | 0\|0 |
| annual_total/2025/GBP/01 | 128\|61517941 | 0\|0 |
| annual_total/2025/GBP/02 | 35\|15922206 | 0\|0 |
| annual_total/2025/USD/01 | 124\|62787575 | 0\|0 |
| annual_total/2025/USD/02 | 38\|16289519 | 0\|0 |
| file_count/2025 | 12 | 0 |
| grand_total/all_years | 3332\|1638019233 | 2856\|1405941313 |
| quarantine_count/2025 | 4 | 0 |

Failure reproduced locally with `python3 scripts/tp_dbx/showcase.py --ns rehprnt recon`
(9/57 checks failing, identical ids and values to the job run).

## Root cause

New monthly drops arrived but were never backfilled — the target was stale, not wrong:

- Landing volume `/Volumes/ow_tp/bronze/landing/rehprnt/history/2025/` contained
  12 new files `CUSTBILL_REHPRNT_202501.dat` … `CUSTBILL_REHPRNT_202512.dat`.
- Bronze `ow_tp.bronze.custbill_history_raw_rehprnt` held only 2019–2024
  (72 files, 3024 rows); expectations `ow_tp.ops.history_expectations_rehprnt`
  already covered 2019–2025 (42 rows, file_count 12 and quarantine 4 for 2025).
- No pipeline SQL defect: every non-2025 check passed, and the deficit in
  `grand_total/all_years` (476 records / 232,077,920 cents) equals exactly the
  expected 2025 totals.

## Remediation

Pure data catch-up — no repo code changed, no legacy scripts touched, no
target tables hand-edited:

```
python3 scripts/tp_dbx/showcase.py --ns rehprnt backfill
```

Bronze reloaded from the landing volume (84 files, 3528 raw rows), silver
rebuilt (3332 rows + 35 quarantined), gold rebuilt (42 annual rows).

## Evidence of fix

- Recon green: 57/57 checks pass, planted anomalies 35/35 matched exactly
  (missing 0, unexpected 0), idempotency rerun pass — see committed report
  `docs/tech-partnerships/recon/custbill_history_backfill-rehprnt.recon.json`.
- Delta time travel on `ow_tp.gold.custbill_annual_rehprnt`:
  - before (v3): `record_count=2856, total_amount_cents=1405941313`
  - after (v5): `record_count=3332, total_amount_cents=1638019233`
- Recon job re-triggered and green:
  https://dbc-8bc9474f-40ae.cloud.databricks.com/jobs/runs/528199017573904
  (task recon_check SUCCESS; notify_devin EXCLUDED — no failure path taken).
- Job schedule remains PAUSED (`0 0 6 * * ?` UTC, pause_status=PAUSED).

# Incident: CUSTBILL history reconciliation failure (ns=reh0818a)

- **Detected by:** Databricks recon job `1034066352684820`, run `349636802873979`
  (failed run: https://dbc-8bc9474f-40ae.cloud.databricks.com/jobs/1034066352684820/runs/349636802873979)
- **Date:** 2026-08-18 (UTC)
- **Severity:** data staleness — migrated target lagged the legacy source; no data corruption.

## Failing checks (from the job's raise_error and an independent local rerun of `showcase.py --ns reh0818a recon`)

9 of 57 checks failed, all pointing at source year 2025 being absent from the target:

| check id | expected | actual |
|---|---|---|
| `annual_total/2025/EUR/01` | 136\|64822494 | 0\|0 |
| `annual_total/2025/EUR/02` | 39\|17581115 | 0\|0 |
| `annual_total/2025/GBP/01` | 116\|54751731 | 0\|0 |
| `annual_total/2025/GBP/02` | 26\|13580310 | 0\|0 |
| `annual_total/2025/USD/01` | 128\|63739115 | 0\|0 |
| `annual_total/2025/USD/02` | 31\|16145019 | 0\|0 |
| `file_count/2025` | 12 | 0 |
| `grand_total/all_years` | 3332\|1659638910 | 2856\|1429019126 |
| `quarantine_count/2025` | 4 | 0 |

Planted-anomaly comparison also showed 5 missing detections (the 2025 anomalies), 0 unexpected.

## Root cause

Twelve new monthly CUSTBILL drops for 2025 (`CUSTBILL_REH0818A_2025MM.dat`) had landed in the
landing volume `/Volumes/ow_tp/bronze/landing/reh0818a/history/2025/` and the expectations table
`ow_tp.ops.history_expectations_reh0818a` already covered 2019–2025 (42 rows, 7 years), but the
bronze/silver/gold tables were never backfilled past 2024:

- landing volume: 84 files across 2019–2025 (12 per year)
- `ow_tp.bronze.custbill_history_raw_reh0818a` before fix: 3024 rows, 72 files, max(source_year)=2024

Target stale relative to arrived source data — not a pipeline-logic or expectations defect. The
locally regenerated manifest (`make legacy-etl-gen-history NS=reh0818a START_YEAR=2019 END_YEAR=2025`)
matches the loaded expectations table exactly, confirming expectations were correct.

## Remediation

Pure data catch-up, no code change: `python3 scripts/tp_dbx/showcase.py --ns reh0818a backfill`
(bronze full historical reload → silver + quarantine → gold). No target table was hand-edited;
no legacy script was touched.

## Evidence

### Delta time travel — `ow_tp.gold.custbill_annual_reh0818a`

Before fix (versions up to v3):

```
totals AS OF v2: record_count=2856 total_amount_cents=1429019126
totals AS OF v3: record_count=2856 total_amount_cents=1429019126
```

After fix (backfill wrote v4/v5):

```
totals AS OF v4: record_count=3332 total_amount_cents=1659638910
totals AS OF v5: record_count=3332 total_amount_cents=1659638910
```

Bronze after backfill: 3528 rows, 84 files, 2019–2025; silver 3332; quarantine 35; gold 42 rows.

### Reconciliation after fix

`docs/tech-partnerships/recon/custbill_history_backfill-reh0818a.recon.json` (committed in this PR):
57/57 checks pass, planted anomalies 35/35 expected==actual (0 missing, 0 unexpected),
idempotency rerun pass (silver/quarantine/gold rebuilt; all checks byte-identical).

### Recon job re-run (green)

https://dbc-8bc9474f-40ae.cloud.databricks.com/jobs/runs/158755016838841 — `recon_check` SUCCESS,
`notify_devin` EXCLUDED (no failure path taken). Job schedule remains PAUSED.

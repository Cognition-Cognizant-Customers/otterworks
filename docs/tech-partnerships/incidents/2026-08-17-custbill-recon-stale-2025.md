# Incident: CUSTBILL history reconciliation failure (ns=demo) — 2026-08-17

## Signal

Scheduled Databricks recon job `ow_tp_billing_history_recon_demo` (job 139369716277099)
failed and webhooked this automation.

- Failed run: https://dbc-8bc9474f-40ae.cloud.databricks.com/jobs/139369716277099/runs/458959840473888
- `recon_check` task raised `[USER_RAISED_EXCEPTION] RECONCILIATION FAILED (9 checks)`.

Failing check ids (expected vs actual):

```
quarantine_count/2025        expected=4              actual=0
file_count/2025              expected=12             actual=0
grand_total/all_years        expected=3332|1675450525  actual=2856|1439098122
annual_total/2025/EUR/01     expected=123|59317751   actual=0|0
annual_total/2025/EUR/02     expected=36|19130256    actual=0|0
annual_total/2025/GBP/01     expected=127|62389054   actual=0|0
annual_total/2025/GBP/02     expected=23|11885067    actual=0|0
annual_total/2025/USD/01     expected=128|63565673   actual=0|0
annual_total/2025/USD/02     expected=39|20064602    actual=0|0
```

## Root cause (evidence from the target tables, not assumption)

Newly arrived 2025 monthly CUSTBILL drops landed in the volume and were expected,
but the migrated target was never backfilled — the target was stale, not wrong:

- Landing volume `/Volumes/ow_tp/bronze/landing/demo/history/` contained year
  directories 2019–**2025**.
- `ow_tp.ops.history_expectations_demo` covered 2019–**2025**
  (2025: record_count=476, total=236,352,403 cents, quarantine=4, files=12).
- `ow_tp.bronze.custbill_history_raw_demo` stopped at **2024** (72 files, 3,024 rows);
  `ow_tp.silver.custbill_history_demo` = 2,856 rows, quarantine = 30,
  `ow_tp.gold.custbill_annual_demo` had no 2025 rows.

Every failing check is a 2025 aggregate (or the grand total that includes 2025):
the pipeline SQL is correct; the source history grew and the target lagged behind.

## Remediation

Smallest correct action: data catch-up only — no code change, no hand-edited tables.

```
make legacy-etl-gen-history NS=demo START_YEAR=2019 END_YEAR=2025   # regenerate expectations manifest
python3 scripts/tp_dbx/showcase.py --ns demo backfill               # bronze -> silver/quarantine -> gold
python3 scripts/tp_dbx/showcase.py --ns demo recon                  # green, report committed
python3 scripts/tp_dbx/showcase.py --ns demo run-job                # re-trigger the Databricks recon job
```

## Delta time-travel evidence (`ow_tp.gold.custbill_annual_demo`)

Before fix (stale):

```
totals AS OF v3: record_count=2856  total_amount_cents=1439098122
```

After backfill:

```
totals AS OF v5: record_count=3332  total_amount_cents=1675450525
```

(Delta history shows the backfill WRITEs v4/v5 on 2026-08-17; v0–v3 predate the fix.)

## Proof it is green

- Local recon: **57/57 checks pass**, planted anomaly sets match exactly
  (expected 35 / actual 35, missing 0, unexpected 0), idempotency rerun **pass**
  ("silver/quarantine/gold rebuilt from bronze; all 57 checks byte-identical").
  Report: `docs/tech-partnerships/recon/custbill_history_backfill-demo.recon.json`
  (failing before-state preserved in `custbill_history_backfill-demo-BEFORE.recon.json`).
- Re-triggered recon job run: **SUCCESS** —
  https://dbc-8bc9474f-40ae.cloud.databricks.com/jobs/runs/18941939532977
- Job schedule remains **PAUSED** (verified via Jobs API after the run).

## Guardrails observed

- Serverless SQL warehouse only (565cd2fd713738c4); no clusters or warehouses created.
- All objects `ow_tp`-prefixed and scoped to namespace `demo`; no DDL outside the namespace.
- Legacy estate under `etl/legacy-extra/jobs/` untouched; no target table hand-edits.

# Incident: CUSTBILL history reconciliation failure — ns `rehearse1` (2026-08-17)

## Trigger
Databricks recon job `1052755456308849` run `57261908373311` (task `recon_check`,
`/Shared/ow_tp/recon_check_rehearse1.sql`) failed at 03:39:00Z and its
`notify_devin` task POSTed the automation webhook that started this remediation
session (namespace `rehearse1`, base branch `devin/tp-dbx-showcase`).

## Failing checks (verbatim from the run's raise_error output)
`RECONCILIATION FAILED (9 checks)`:

| check id | expected | actual |
|---|---|---|
| `quarantine_count/2025` | 4 | 0 |
| `grand_total/all_years` | 3332\|1674850589 | 2856\|1440462121 |
| `annual_total/2025/GBP/02` | 33\|16861540 | 0\|0 |
| `annual_total/2025/USD/02` | 29\|15956966 | 0\|0 |
| `annual_total/2025/EUR/01` | 130\|63770928 | 0\|0 |
| `annual_total/2025/USD/01` | 136\|67229361 | 0\|0 |
| `annual_total/2025/EUR/02` | 37\|19079080 | 0\|0 |
| `annual_total/2025/GBP/01` | 111\|51490593 | 0\|0 |

## Root cause
Target stale after new monthly drops: every 2025 aggregate in
`ow_tp.gold.custbill_annual_rehearse1` was 0 while the expectations table
carried 2025 totals, and the grand total was short by exactly the 2025
contribution (3332−2856 = 476 records). The 2025 `CUSTBILL_REHEARSE1_2025MM.dat`
drops were present in `/Volumes/ow_tp/bronze/landing/rehearse1/history/2025/`
at diagnosis time (03:41Z) but had never been backfilled bronze→silver→gold.
Classic "new history arrived, target never backfilled" drift; no pipeline SQL
defect. The intended fix was `python3 scripts/tp_dbx/showcase.py --ns rehearse1
backfill` followed by a recon rerun.

## Why remediation was not executed on `rehearse1`
The namespace was torn down by its owning rehearsal run ~100 seconds after the
webhook fired, while this session was diagnosing:

- `SHOW TABLES DROPPED` (Unity Catalog): `gold.custbill_annual_rehearse1`
  deleted 03:40:49Z; `silver.custbill_history_rehearse1` and
  `silver.custbill_quarantine_rehearse1` 03:40:50Z;
  `bronze.custbill_history_raw_rehearse1`, `ops.history_expectations_rehearse1`,
  `ops.recon_runs_rehearse1` 03:40:51–52Z; DLT tables 03:41:21Z.
- The landing files under `/Volumes/ow_tp/bronze/landing/rehearse1/` (present at
  03:41Z), the `/Shared/ow_tp/*_rehearse1*` workspace objects, the DLT pipeline,
  and recon job `1052755456308849` itself were all deleted by 03:43Z.

Per the tech-partnerships reproducibility policy, rehearsal namespaces are
destroyed after their run and teardown is owned by the parent run. Rebuilding
`rehearse1` solely to replay the fix would recreate throwaway state and contend
with the owning run, so it was intentionally not done.

## Verification on the persistent namespace (`demo`)
The same pipeline and recon path were proven green where the estate persists:

- Expectations regenerated deterministically:
  `make legacy-etl-gen-history NS=demo START_YEAR=2019 END_YEAR=2025`.
- `showcase.py --ns demo recon`: **57 checks, 0 failed; planted anomalies
  35/35 matched as sets (0 missing / 0 unexpected); idempotency rerun pass** —
  report committed at
  `docs/tech-partnerships/recon/custbill_history_backfill-demo.recon.json`.
- Recon job re-triggered (`showcase.py --ns demo run-job`): **SUCCESS** —
  https://dbc-8bc9474f-40ae.cloud.databricks.com/jobs/runs/241648299586757
  (`recon_check` SUCCESS, `notify_devin` EXCLUDED). Job schedule remains paused.
- Delta time travel on `ow_tp.gold.custbill_annual_demo` (7 versions): totals
  AS OF v5 and v6 both `record_count=3332`, `total_amount_cents=1675450525` —
  stable before/after the recon window.

## Follow-ups
- Rehearsal teardown raced the auto-remediation webhook. If remediation
  rehearsals should complete, teardown should wait for (or cancel) the
  in-flight remediation session, or the webhook should not fire on runs whose
  namespace is about to be destroyed.

No application or pipeline code required changes; the legacy estate and golden
application path were not touched.

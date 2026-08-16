# Contract: `run_all.sh` + estate rollup → `ow_tp_estate_orchestrator` / `ow_tp_estate_rollup`

Second wave. Read [README.md](README.md) first — the shared rules there are part of this
contract. This unit may only start once the silver tables from the per-script wave exist.

| | |
|---|---|
| Sources | `etl/legacy-extra/run_all.sh` (bash, 2014) and `etl/legacy-extra/crontab` |
| Converted jobs | `ow_tp_estate_orchestrator`, `ow_tp_estate_rollup` (`infrastructure/terraform-databricks/jobs_estate_rollup.tf`) |
| Position | orchestration + gold across all units |

## Deficiencies this conversion must retire

- `sleep 600` as dependency management — downstream runs on partial data if upstream is
  slow → a multi-task Databricks job whose tasks declare `depends_on`, so ordering is a DAG
  edge and not a wall-clock guess.
- Overlapping cron schedules with no cross-job locking (ingest `:00`/`:15` overlaps itself;
  the finance report overlaps `analytics_daily`) → one orchestrator with
  `max_concurrent_runs = 1` and explicit dependencies.
- Failures invisible: every stage suppressed with `2>/dev/null || true` → a failed task
  fails the run.
- No estate-level view of whether a night's batch actually reconciled → gold rollup table.

## Target

| Object | Contents |
|---|---|
| `ow_tp_estate_orchestrator` | multi-task serverless job wiring the converted units in dependency order: `sftp_ingest` → `parse_custbill` → `finance_report`, and the Python-wave jobs, ending in `estate_rollup`. No `sleep`, no cluster. |
| `ow_tp.gold.estate_daily_rollup` | one row per (`ns`, `run_date`, `unit`): `legacy_source`, `language_vintage`, `rows_in`, `rows_out`, `rejected`, `recon_result`, `recon_detail`, `job_run_id`, `updated_at`. Reads only the silver/gold tables the per-script wave produced. |
| `ow_tp.gold.estate_anomalies` | anomalies surfaced from the seeded data / seed manifest: `ns`, `unit`, `anomaly_type`, `detail`, `detected_at`. The legacy estate surfaces none of these; the conversion must. |

## Golden legacy baseline

There is no single legacy artifact for the estate; the baseline is the union of the
per-unit golden outputs under `/home/ubuntu/tp-golden/` plus the per-unit recon reports
merged bottom-up. The orchestrator's own baseline is `make legacy-etl-run JOB=run_all`
(`RUN_ALL_SLEEP=0` preset), which must produce the same CUSTBILL artifacts as the three
stages run individually — that equivalence is what the DAG has to preserve.

## Acceptance checks (`scripts/tp_databricks/recon_estate_rollup.py`)

1. `gold.estate_daily_rollup` has one row per converted unit for `ns='demo'`, and every
   `recon_result` value is derived from that unit's recon script output — not hand-entered.
2. The rollup's CUSTBILL numbers cross-foot to the legacy finance baseline to the cent:
   100 parsed rows; INVOICE EUR 22/101554.41, GBP 32/183113.58, USD 28/130502.15;
   CREDIT EUR 6/33375.97, GBP 5/28454.59, USD 7/33390.44.
3. A deliberately failing upstream task must fail the orchestrator run and must **not**
   leave a green `recon_result` in the rollup — prove this with an actual run, and revert
   any test artifact afterwards.
4. `gold.estate_anomalies` contains the anomalies present in the seeded namespace, each
   traceable to a seed-manifest entry; an empty table is only acceptable if you show the
   manifest planted none.
5. Orchestrator declares `max_concurrent_runs = 1`, contains no `sleep`-based ordering, and
   creates no cluster (serverless tasks only).

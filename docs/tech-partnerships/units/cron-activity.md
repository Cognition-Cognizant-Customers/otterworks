# Cron activity

This unit rewrites `etl/scripts/user_activity_daily.py`. The extractor backfills
pre-run-date history into the `ow_tp` landing volume; the daily job consumes the
cron-analytics gold tables and publishes an idempotent activity report.

## Parent migration commands

```sh
NS=demo DS=2026-01-15
PYTHON=$(scripts/tp_cronbox/ensure_venv.sh)
AWS_ENDPOINT_URL=http://localhost:4566 "$PYTHON" scripts/tp_cron_activity/extract_history.py --ns "$NS" --ds "$DS" --target databricks
cd infrastructure/databricks/cronbox
DATABRICKS_HOST=${DATABRICKS_DEMO_HOST} DATABRICKS_TOKEN=${DATABRICKS_DEMO_TOKEN} mise exec -- databricks bundle deploy
DATABRICKS_HOST=${DATABRICKS_DEMO_HOST} DATABRICKS_TOKEN=${DATABRICKS_DEMO_TOKEN} mise exec -- databricks bundle run ow_tp_cron_activity_history_backfill --params ns="$NS",ds="$DS"
DATABRICKS_HOST=${DATABRICKS_DEMO_HOST} DATABRICKS_TOKEN=${DATABRICKS_DEMO_TOKEN} mise exec -- databricks bundle run ow_tp_cron_user_activity_daily --params ns="$NS",ds="$DS"
cd -
make tp-cron-activity-recon NS="$NS" DS="$DS"
```

Deployment and job execution are parent-only operations. The child implementation
session must not run `bundle deploy`, `bundle run`, or `jobs run-now`; the commands
above document the exact parent invocation. The CLI help references are:
`mise exec -- databricks bundle run --help` and
`mise exec -- databricks jobs run-now --help`.

## Public gold interface

The unit owns `ow_tp.gold.user_activity_daily`, `user_activity_user_summaries`,
`user_activity_window_coverage`, and the `user_activity_latest` view. All rows
are scoped by `(namespace, report_date)`. The report has the legacy scalar,
trends, daily summaries, ordered user summaries, and top users; `generated_at`
is intentionally omitted because it is volatile.

## Acceptance mapping

| Contract | SQL / recon evidence |
|---|---|
| ACT-01 | `40_window_coverage.sql`, `60_gold_report.sql`; `ACT-01/summary_window_row_count`, `ACT-01/summary_window_dates`, `ACT-01/summary_window_values`, `ACT-01/no_legacy_source_references` |
| ACT-02 | `51_user_summaries_publish.sql`; `ACT-02/user_row_count`, `ACT-02/user_aggregates` |
| ACT-03 | `40_window_coverage.sql`, `20_gold_merge_summary_history.sql`, `21_gold_merge_top_users_history.sql`; `ACT-03/history_days_present`, `ACT-03/missing_history_days`, `ACT-03/gap_day_contributes_nothing`, `ACT-03/job_run_succeeded`, `ACT-03/analytics_rundate_row_intact` |
| ACT-04 | `33_ddl_gold_latest_view.sql`; `ACT-04/latest_resolves_to_run_date`, `ACT-04/latest_matches_dated_report` |
| ACT-05 | `50_user_summaries_prune.sql`, `51_user_summaries_publish.sql`, `60_gold_report.sql`; `ACT-05/no_duplicate_users_after_rerun`, `ACT-05/report_row_singleton_after_rerun`, `ACT-05/values_stable_across_rerun` |
| ACT-06 | `51_user_summaries_publish.sql`, `60_gold_report.sql`; `ACT-06/report_trends`, `ACT-06/report_daily_summaries`, `ACT-06/report_user_summaries`, `ACT-06/report_top_users`, `ACT-06/user_order`, `ACT-06/user_summaries_jsonl_equivalent`, `ACT-06/report_scalar_fields`, `ACT-06/generated_at_volatile` |

The exact recon commands are:

```sh
python3 scripts/tp_cron_activity/recon.py --mode fixture --ns "$NS" --ds "$DS" \
  --out docs/tech-partnerships/recon/cron-activity-demo.fixture.recon.json
python3 scripts/tp_cron_activity/recon.py --mode live --ns "$NS" --ds "$DS" \
  --warehouse-id 565cd2fd713738c4 \
  --out docs/tech-partnerships/recon/cron-activity-demo.recon.json
```

The live recon defaults to `--rerun-mode job`; `--rerun-mode sql` is available
for a parent-controlled SQL rerun.

`actions_by_type` map key order is not preserved by Spark; recon compares key/value
pairs as sorted items. Missing user IDs are attributed to `unknown` in gold.
The empty-window path writes an empty report; its average is numeric `0.0` while
the legacy JSON may render `0`. The baseline has no root `stdout.log`.

## Coverage

The backfill job must run before the daily job. The fixture recon proves
deterministic extraction, the strict `< ds` guard, missing-day reporting,
UTF-8 transport, and offline parity helpers. `LAND-08` is skipped because the
committed fixture corpus is clean; malformed JSON and invalid UTF-8 attribution
are exercised by the unit tests and listed as an unverified fixture path.
Target SQL, Delta MERGE behavior, view resolution, job shape, PAUSED schedule,
and rerun idempotency require the parent's live Databricks window. The report
intentionally omits `generated_at`; map key order is not a bitwise contract,
and the baseline has no root `stdout.log`.

# Contract: `user_activity_daily.py` → `ow_tp_user_activity`

Read [README.md](README.md) and [_python_wave_baseline.md](_python_wave_baseline.md) first.

| | |
|---|---|
| Source | `etl/scripts/user_activity_daily.py` |
| Language / vintage | Python 2014-vintage (psycopg2 + boto3 + pandas) |
| Legacy schedule | daily 05:00 UTC |
| Converted job | `ow_tp_user_activity` (`infrastructure/terraform-databricks/jobs_user_activity.tf`) |
| Depends on | the analytics unit's `gold.analytics_daily_summary` (the legacy script reads `analytics_daily_summary`, i.e. `analytics_daily.py`'s output — the dependency cron never expressed) |

## Deficiencies this conversion must retire

Reads the previous job's output table with no check that the previous job ran or succeeded
(cron ordering by clock: 02:00 then 05:00 — if analytics is late or failed, this job
silently reports on stale or partial data) → explicit task dependency and a freshness
assertion. Plus: hardcoded DB password and AWS keys; per-user S3 reads in a Python loop
(→ one set-based read); pandas in-memory aggregation; `print()` logging; silent
`except: pass`; no retry; no idempotency; report delivered to admin-service with no
verification.

## Target

| Object | Contents |
|---|---|
| `ow_tp.bronze.user_activity_raw` | per-user source rows joined from the analytics aggregate and the per-user event data: `ns`, `user_id`, `activity_date`, `metric`, `value`, `source`, `ingested_at` |
| `ow_tp.silver.user_activity_daily` | one row per (`ns`, `user_id`, `activity_date`): document/file/event counts, active-hours, typed columns |
| `ow_tp.gold.user_activity_report` | the report the legacy job generated: `ns`, `report_date`, `user_id`, `documents_touched`, `files_touched`, `events`, `last_active_ts`, plus `upstream_summary_date` and `upstream_fresh BOOLEAN` — the freshness fact cron could not express |

## Baseline

Legacy run on this VM **failed** (`/home/ubuntu/tp-golden/python/user_activity_daily/`):

```text
Querying PostgreSQL for analytics aggregates (lookback: 30 days)...
ERROR: PostgreSQL query failed: connection to server at "localhost" (::1), port 5432 failed:
FATAL: password authentication failed for user "otterworks"
```

Two blockers: the Postgres endpoint (the reachable seeded instance is
`localhost:55432`, container `otterworks-postgres-alt`; host port 5432 is occupied by an
unrelated Postgres that rejects these credentials) and `analytics_daily_summary`, which
only exists once `analytics_daily.py` has run — and that script is itself blocked (see
[analytics_daily.md](analytics_daily.md)). Tier 1 attempt: point the script at
`localhost:55432` via a scratch config outside the repo, and populate
`analytics_daily_summary` from the seeded event data using the same grain the legacy
analytics script would produce — if you do this, the aggregate is a **fixture you built**,
so say exactly how you built it in the report. Otherwise fall back to the seed-manifest
tier.

## Acceptance checks (`scripts/tp_databricks/recon_user_activity.py`)

1. Per-user parity: `gold.user_activity_report` for `ns='demo'` matches the baseline
   row-for-row on `user_id` × `report_date`, with every count exactly equal. The seed's
   ownership distribution is a power law, so include the top-N whale users **and** the long
   tail in the comparison, not a head sample.
2. Totals cross-foot: summed per-user counts equal the totals in the upstream analytics
   aggregate — gold cannot exceed or lose rows relative to its source.
3. Freshness guard: with a stale or missing upstream summary, the run fails (or records
   `upstream_fresh = false` and produces no report) instead of silently reporting stale
   data. Demonstrate it — this is the deficiency this unit exists to retire.
4. Re-run; assert one row per user per date, no duplication (idempotency).
5. Report states the baseline tier verbatim and, if a fixture aggregate was built, exactly
   how.

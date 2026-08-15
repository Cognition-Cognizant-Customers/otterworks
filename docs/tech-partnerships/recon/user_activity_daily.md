# Recon — user_activity_daily -> ow_tp_user_activity

baseline: legacy output

## Baseline provenance

Tier 1 per `_python_wave_baseline.md`: the numbers below come from running the
**unmodified** `etl/scripts/user_activity_daily.py` on this VM and capturing its
output under `/home/ubuntu/tp-golden/python/user_activity_daily` (`exit_code=exit=0`).
Nothing in the legacy output was regenerated, edited or synthesised by the
conversion, and the conversion is never compared against itself.

Standing the unit up required two documented local fixtures, neither of which is
the baseline itself:

1. **Postgres on 55432.** Host port 5432 was already occupied by a Postgres that
   rejects the `otterworks` credentials, so the fixture ran in container
   `otterworks-postgres-alt` on 55432 (`DB_PORT=55432`), and the legacy script was
   pointed at it with a scratch copy of `etl/config.ini` **outside** the repo
   (`/home/ubuntu/tp-scratch/etl-config/config.ini`). Nothing under `etl/` was edited.
2. **The upstream analytics aggregate.** The legacy job reads
   `analytics_daily.py`'s output. That job could **not** be run here — it requires
   production-shaped SQS/DynamoDB resources this VM does not have — so
   `scripts/tp_databricks/fixture_analytics_upstream.py` derived the aggregate
   (`analytics_daily_summary` rows plus the per-day `top_users.jsonl.gz` objects)
   deterministically from the same seeded events (`make seed-legacy NS=demo`).
   **The real `analytics_daily.py` did not run.** The legacy user-activity script
   itself ran unmodified against that aggregate, and its output is the baseline.

Legacy report: date `2026-08-15`, lookback `30` days, `trends.total_events` = 5147, 50 user summaries.

Baseline hashes (`manifest_sha256.txt`):

```
0d11d98abfb75d8ddecab0097ff89f69fe02f9a39ea10da1d36ca516cfc5eebe  activity_report.json
181681cd84c1967e98ad91a1b30f9ec359caec4606e015ea3cffec1d3bbd567d  user_summaries.jsonl
```

## How the converted side was produced

The job (`ow_tp_user_activity`, PR 1/3) was **not** applied — the parent session owns
workspace state. Instead `scripts/tp_databricks/run_user_activity.py` executed the
job task's notebook `main()` against the existing `Serverless Starter Warehouse`, so
the statements reconciled here are the statements the job runs. No cluster was
created and no throwaway job was left behind.

### The documented volume upload path is UNVERIFIED

The production transport for this unit is the landing volume `/Volumes/ow_tp/bronze/landing`, read set-based by the notebook's
`read_files()` (`source_mode=volume`), and that path is **unverified here**: the demo PAT lacks the `files` scope, so every
`dbx.py upload` to the volume is refused with exactly

```
HTTP Error 403: Forbidden
Provided access token does not have required scopes: files
```

The evidence below was therefore produced with the in-Databricks fallback: `land_user_activity.py` landed the inputs into `ow_tp.bronze.user_activity_events_landed` and the notebook read them with `source_mode=table`.
That table is a workaround for a token limitation, **not** the production transport, and it is not presented as one; the volume wiring is unchanged and remains the
job's default. No check was loosened to compensate — the aggregation, the freshness guard and every comparison below are identical in both modes; only the two lines
that read the landed events differ. Proving the volume leg needs a token with the `files` scope.

Parity was reproduced with `max_upstream_lag_days=30`, matching the
legacy behaviour: the baseline was captured with `ds=2026-08-15` over seeded events
that end `2026-07-31`, i.e. the legacy script reported over a 15-day-old aggregate
without noticing. The job's default tolerance is 1 day and **refuses** that exact
run — see check 3.

## Result: green

### 1. Per-user parity (user_id x report_date, exact counts) — PASS

```json
{
  "legacy_users": 50,
  "converted_users": 50,
  "coverage": {
    "legacy_users_in_baseline": 50,
    "legacy_total_users_reported": 50,
    "legacy_artefact_truncated": false,
    "comparison_scope": "every user on both sides"
  },
  "missing_users": [],
  "unexpected_users": [],
  "mismatches": [],
  "sample": [
    {
      "user_id": "f3eba98b-b5c9-4492-8743-43fb3b37c76d",
      "band": "whale",
      "legacy_events": 780,
      "converted_events": 780,
      "legacy_active_days": 3,
      "converted_active_days": 3
    },
    {
      "user_id": "7e61438d-dc5b-4f15-9f95-773e5c8f51e1",
      "band": "whale",
      "legacy_events": 326,
      "converted_events": 326,
      "legacy_active_days": 3,
      "converted_active_days": 3
    },
    {
      "user_id": "3eff61d0-df70-4de1-86ba-e6b112862e0b",
      "band": "whale",
      "legacy_events": 252,
      "converted_events": 252,
      "legacy_active_days": 3,
      "converted_active_days": 3
    },
    {
      "user_id": "b5d93127-3ff3-4e7c-82a8-01cdfc873638",
      "band": "long tail",
      "legacy_events": 46,
      "converted_events": 46,
      "legacy_active_days": 3,
      "converted_active_days": 3
    },
    {
      "user_id": "d2af3bd4-cfe6-463b-ab54-52f2447021c7",
      "band": "long tail",
      "legacy_events": 44,
      "converted_events": 44,
      "legacy_active_days": 3,
      "converted_active_days": 3
    },
    {
      "user_id": "347559b1-79b1-4895-b096-5d02abef715e",
      "band": "long tail",
      "legacy_events": 40,
      "converted_events": 40,
      "legacy_active_days": 3,
      "converted_active_days": 3
    }
  ]
}
```

### 2. Totals cross-foot (no rows lost or invented) — PASS

```json
{
  "values": {
    "converted per-user sum": 5147,
    "legacy report trends.total_events": 5147,
    "upstream aggregate SUM(total_events)": 5147,
    "legacy per-user sum": 5147
  }
}
```

### 3. Freshness guard refuses stale/missing upstream — PASS

```json
{
  "report_rows_before": 50,
  "upstream_rows_recovered_from_leftover_backup": 0,
  "upstream_rows_saved": 3,
  "upstream_rows_restored": 3,
  "scenarios": [
    {
      "refused": true,
      "detail": "upstream latest report_date 2026-07-31 lags report_date 2026-08-15 by 15 days (limit 1)",
      "scenario": "stale upstream, default 1-day tolerance",
      "report_rows_after": 50,
      "report_rows_unchanged": true,
      "run_log": [
        "refused_stale_upstream",
        "false",
        "0"
      ]
    },
    {
      "refused": true,
      "detail": "upstream ow_tp.bronze.user_activity_upstream_fixture has no rows for ns=demo inside the 30-day window ending 2026-08-15: the analytics job has not run for this window",
      "scenario": "missing upstream (analytics job never ran)",
      "report_rows_after": 50,
      "report_rows_unchanged": true,
      "run_log": [
        "refused_missing_upstream",
        "false",
        "0"
      ]
    }
  ]
}
```

### 4. Idempotency (re-run duplicates nothing) — PASS

```json
{
  "values": {
    "rows before re-run": 50,
    "rows after re-run": 50,
    "distinct users": 50,
    "duplicate user/date keys": 0
  }
}
```

### 5. Baseline provenance stated (tier 1, legacy output) — PASS

```json
{
  "values": {
    "tier": "baseline: legacy output",
    "legacy_exit_code": "exit=0",
    "baseline_artefacts": {
      "activity_report.json": true,
      "user_summaries.jsonl": true,
      "manifest_sha256.txt": true
    },
    "analytics_daily.py executed": false,
    "upstream aggregate": "deterministic fixture from the seeded events"
  }
}
```

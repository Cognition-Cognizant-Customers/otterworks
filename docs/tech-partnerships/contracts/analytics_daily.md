# Contract: `analytics_daily.py` → `ow_tp_analytics_daily`

Read [README.md](README.md) and [_python_wave_baseline.md](_python_wave_baseline.md) first.

| | |
|---|---|
| Source | `etl/scripts/analytics_daily.py` |
| Language / vintage | Python 2014-vintage (boto3 + psycopg2 + pandas) |
| Legacy schedule | daily 02:00 UTC |
| Converted job | `ow_tp_analytics_daily` (`infrastructure/terraform-databricks/jobs_analytics_daily.tf`) |

## Deficiencies this conversion must retire

From `etl/ETL_UPGRADE_GUIDE.md`: hardcoded AWS keys and DB password in `etl/config.ini`
(→ secret scope `ow_tp`); no retry on transient AWS failures (the SQS extract gives up
after 3 tries and continues with zero events — silent data loss); `print()` logging; silent
`except: pass`; pandas in-memory aggregation with a row-by-row loop (→ set-based SQL /
Spark aggregation); monolithic `main()`; no idempotency (a re-run duplicates rows); no
alerting; cron with no dependency management, and a 02:10 overlap with the finance report.

## Target

| Object | Contents |
|---|---|
| `ow_tp.bronze.analytics_events_raw` | events as ingested: `ns`, `event_id`, `event_type`, `user_id`, `document_id`, `file_id`, `event_ts`, `source` (`sqs`/`dynamodb`/`s3`), `raw_payload`, `ingested_at` |
| `ow_tp.silver.analytics_events` | deduplicated, typed, one row per `event_id`, invalid/unparseable events quarantined in `ow_tp.silver.analytics_events_rejects` |
| `ow_tp.gold.analytics_daily_summary` | the aggregate the legacy script wrote to S3 gzip JSON and Postgres: `ns`, `summary_date`, `hour`, `user_id`, `document_id`, `file_id`, `event_type`, `event_count`. Same grain as the legacy output, computed in SQL. |

Idempotency: re-running for the same `ns`/date replaces rather than appends.

## Baseline

Legacy run on this VM **failed** (`/home/ubuntu/tp-golden/python/analytics_daily/`):

```text
WARNING: SQS receive failed (1 consecutive) ... ERROR: Too many SQS failures, giving up
Extracted 0 events from SQS
FATAL: An error occurred (ResourceNotFoundException) when calling the Scan operation: Cannot do operations on a non-existent table
```

It needs a reachable SQS queue (the URL is hardcoded to a production endpoint) and the
DynamoDB table `otterworks-analytics-events`. Tier 1 attempt: create both in LocalStack and
populate the table from the seeded event objects in `s3://otterworks-data-lake/events/demo/`
(71 hourly gzip JSON objects, 340,945 bytes), then run the script against a scratch config.
If the hardcoded queue URL cannot be redirected without editing the script, say so
explicitly and fall back to the seed manifest tier — do not edit `etl/scripts/`.

## Acceptance checks (`scripts/tp_databricks/recon_analytics_daily.py`)

1. Event-count parity: total events in `silver.analytics_events` for `ns='demo'` equals
   the baseline event count (legacy output if tier 1, else the seeded event count derived
   by `testdata/legacy/validate.py`), with zero silent drops — anything not in silver is in
   the rejects table with a reason, and `silver + rejects = bronze`.
2. Aggregate parity: `gold.analytics_daily_summary` matches the baseline aggregates
   exactly on every group (`summary_date`, `hour`, `user_id`, `document_id`, `event_type`) —
   counts are integers, so this is exact equality, no tolerance.
3. The retry deficiency is retired: show that a transient source failure fails the run
   rather than producing a zero-event "success" (the legacy behaviour above).
4. Re-run; assert no duplication (idempotency).
5. Report states the baseline tier verbatim.

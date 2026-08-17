# Cron Analytics

This unit replaces the immutable `etl/scripts/analytics_daily.py` batch with a
transport-preserving extractor and a paused 15-step Databricks SQL job. The parent owns
the only live deployment and recon window.

## Runbook

Start the local fixture estate, seed it before each extraction (the legacy
consumers drain their inputs), and land the deterministic JSONL payload:

```sh
make infra-up
make cronbox-seed NS=demo
make tp-cron-analytics-extract NS=demo DS=2026-01-15
make cronbox-seed NS=demo
make tp-cron-analytics-extract NS=demo DS=2026-01-15
make tp-cron-analytics-verify NS=demo DS=2026-01-15
```

The second seed/extract supplies the rerun evidence used by the fixture report.
The extractor does not delete SQS messages. `--target databricks` is reserved
for the parent live run; local verification uses `local-fixture`.

The parent runs the single documented live reconciliation command:

```sh
make tp-cron-analytics-recon NS=demo DS=2026-01-15 OUT=docs/tech-partnerships/recon/cron-analytics-demo.recon.json
```

## Public gold interface

All interfaces are scoped by `(namespace, report_date)`:

* `ow_tp.gold.analytics_daily_summary`: `report_date DATE`, `namespace STRING`,
  `active_users INT`, `active_documents INT`, `active_files INT`,
  `total_events BIGINT`, `documents_created INT`, `documents_edited INT`,
  `comments_added INT`, `files_uploaded INT`, `files_shared INT`,
  `files_deleted INT`, `bytes_uploaded BIGINT`, and volatile `updated_at TIMESTAMP`.
* `ow_tp.gold.analytics_daily_top_users`: `report_date DATE`, `namespace
  STRING`, `user_id STRING`, `action_counts MAP<STRING,BIGINT>`,
  `total_actions BIGINT`, `user_rank INT`, `first_seq BIGINT`, and volatile
  `updated_at TIMESTAMP`. Rows are ranked top-100 by action count and stable
  first extraction sequence.
* `ow_tp.gold.analytics_hourly_breakdown`: `report_date DATE`, `namespace
  STRING`, `event_hour STRING`, `event_type STRING`, `event_count BIGINT`, and
  volatile `updated_at TIMESTAMP`.
* `ow_tp.gold.analytics_daily_report`: `report_date DATE`, `namespace STRING`,
  and `report_json STRING`, a derived view rendering the legacy daily
  `report.json` shape from the three gold interfaces. The volatile
  `generated_at` field is intentionally absent.

The later `cron-activity` unit consumes these three gold tables for its
30-day lookback and downstream activity report.

## Coverage notes

Invalid-UTF-8 message bodies cannot actually be transported through SQS/boto3
string bodies. The envelope's `raw_body_b64`/`decode_error` path and its
`invalid_utf8_body` reject reason therefore exist to satisfy the contract's
encoding policy, but are not exercised by the seeded estate. The exercised
reject path is the eight non-JSON SQS bodies.

The extractor scans DynamoDB with the run-MONTH prefix. The run-DAY prefix rule
is applied in `30_silver.sql`, preserving the legacy semantics while keeping
the 16 adjacent-day exclusions auditable and provable from the target bronze
data.

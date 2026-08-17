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

The parent deploys the bundle, lands the batch into the real landing volume, then
runs the single documented live reconciliation command:

```sh
databricks bundle deploy -t demo            # from infrastructure/databricks/cronbox/
make cronbox-seed NS=demo                   # inputs for the extraction
make tp-cron-analytics-extract NS=demo DS=2026-01-15 TARGET=databricks
make tp-cron-analytics-recon NS=demo DS=2026-01-15 OUT=docs/tech-partnerships/recon/cron-analytics-demo.recon.json
```

The recon needs a populated slice to compare against. If the target objects do
not exist yet, or hold nothing for `(ns, ds)` — the normal state right after a
deploy, since the job is PAUSED and has never run — it runs the job once to
populate the slice, reads its `before` values from that run, and reports the
priming in `idempotency_rerun.evidence`. The ANL-06 comparison is therefore
always between two populated runs, never between an empty slice and a full one.

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

Deduplication in the extractor is per source, because bronze identity is
`(namespace, report_date, source, source_id)` and the legacy job concatenated the
two streams: the same `event_id` arriving on the queue and in the table is two
events. Within the SQS stream, `source_id` is the payload `event_id` when present
and otherwise `sha256:<hexdigest>` of the raw body, so two distinct messages with
byte-identical bodies and no `event_id` collapse to one bronze row. That is the
price of an id that is stable across reruns (an SQS `MessageId` changes on every
reseed and would make the landing file non-reproducible), and the seeded estate
carries an `event_id` on every event.

The malformed-body predicate keys on a `_corrupt_record` field in the `from_json`
schema, not on a NULL parse result: `from_json` is PERMISSIVE, so an unparseable
body yields a struct of all-NULL fields. Verified read-only on the serverless
warehouse — `from_json('not-json-1', <schema>) IS NULL` is `false`, while the
corrupt-record field is populated; `is_valid_json` is not resolvable on this
runtime. `20_bronze_reject.sql` and `30_silver.sql` use exactly complementary
predicates, so no row is both rejected and aggregated.

The extractor scans DynamoDB with the run-MONTH prefix. The run-DAY prefix rule
is applied in `30_silver.sql`, preserving the legacy semantics while keeping
the 16 adjacent-day exclusions auditable and provable from the target bronze
data.

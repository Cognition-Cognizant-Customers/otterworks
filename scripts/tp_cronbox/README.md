# Cron Box fixture estate

This additive harness runs the five immutable jobs in real cron order:
`analytics_daily`, `storage_cleanup_daily`, `audit_archive_weekly`,
`search_reindex_weekly`, then `user_activity_daily`.

```bash
make infra-up
make cronbox-seed NS=demo
make cronbox-run-all NS=demo
make cronbox-capture NS=demo JOB=analytics_daily
make cronbox-down
```

The fixture anchor and frozen clock are `2026-01-15T00:00:00Z`. Namespace RNG
uses `int(sha256(ns)[:8], 16)`, timestamps never use wall-clock time, and gzip
members use `mtime=0`. Seeding clears the local fixture stores before writing.
Because jobs consume SQS messages, delete audit rows, move orphan objects, and
replace indexes, every run-all must start with `cronbox-seed`.

**Important shared-table warning:** Cron Box seeding takes over the shared
LocalStack `otterworks-audit-events` table and reshapes it to the composite
`event_id` + `timestamp` key required by the immutable archive job. Do not run
the golden app's audit service concurrently with a Cron Box seed or run. Use
`make cronbox-reset` (also run by `make cronbox-down`) to restore the golden
app's original `id` HASH table shape before using the golden app.

The estate provisions LocalStack S3/SQS/DynamoDB, local Postgres database
`otterworks_analytics`, a local corpus API on port 8088, and uses the existing
MeiliSearch container. It includes malformed SQS bodies, unknown-user events,
adjacent-day DynamoDB boundary events, Unicode payloads, S3/reverse metadata
orphans, one missing history partition, and audit cutoff probes at -1/0/+1
seconds. The seed manifest is written to
`testdata/legacy/golden/cronbox/<ns>/seed-manifest.json`; captured job
snapshots are under `testdata/legacy/golden/cronbox/<ns>/<job>/`.

The corpus API serves exactly 125 documents from the Postgres
`cronbox_documents` table and 72 file records from the seeded DynamoDB
file-metadata slice. This is the deterministic corpus the Atlas child must
load.

`sitecustomize.py` replaces `time.sleep` with a no-op only inside the fixture
runner. Frozen libfaketime makes the legacy polling sleep fail with
`EINVAL`; this changes timing only, not emitted values, and leaves legacy
sources untouched. Captured Postgres aggregate values intentionally exclude
`updated_at`, because the immutable SQL writes database `NOW()` and that value
is wall-clock-dependent. The runner also canonicalizes report-only
`generated_at` fields to the fixture anchor after each immutable job, so every
captured object is byte-stable without changing the legacy source or any
business value.

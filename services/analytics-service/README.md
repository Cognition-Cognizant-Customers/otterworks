# Analytics Service

Scala 3 / Akka HTTP service that ingests platform events (via the REST API and
an SQS consumer) and serves aggregated analytics: dashboard summaries, per-user
activity, document stats, top content, active users, storage usage, and report
exports.

## Metrics store

Events and a daily aggregate rollup are persisted to a **durable PostgreSQL
store via Slick** — the golden-app default. The schema is applied with Flyway
from `src/main/resources/db/migration` (the same convention the JVM services in
this repo use):

- `analytics_events` — the raw event log (source of truth). The event instant is
  stored as epoch-nanoseconds (UTC) so it round-trips exactly regardless of DB
  timestamp precision or server time zone.
- `analytics_daily_metrics` — a materialized daily rollup (`event_date`,
  `event_type` → `event_count`) maintained transactionally on every write.

Query responses are derived from the durable event log via the pure,
storage-agnostic `MetricsAggregator`, which is shared with the in-memory backend
so both produce **byte-for-byte identical** results for the same event set.

### Backends

Selected by `analytics.repository.backend` (env `ANALYTICS_REPOSITORY_BACKEND`):

| value        | use                                                        |
|--------------|------------------------------------------------------------|
| `postgres`   | durable store (default; golden app)                        |
| `in-memory`  | ephemeral, process-local — for local runs and unit tests   |

The connection is assembled from `POSTGRES_*` (compose) unless `DATABASE_URL`
is provided explicitly (`scripts/deploy-dev.sh` wiring); `DATABASE_*` always
takes precedence. If the durable store cannot be initialised at startup, the
service logs a warning and falls back to the in-memory store so it still boots.

## Lakehouse migration — "before" state

This durable PostgreSQL store is the **"before"** state for a
REFACTOR / RE-ARCHITECT exercise that moves the analytics store to an
**S3 + Apache Iceberg lakehouse**. It is intentionally shaped so that migration
is a self-contained, verifiable step; the "after" is **not** built here.

### Target ("after") — outline only

- **Storage:** raw events land in S3 (partitioned by `event_date` /
  `event_type`) as an **Apache Iceberg** table; the daily rollup becomes an
  Iceberg aggregate table.
- **Catalog + query:** **AWS Glue Data Catalog** for table metadata and
  schema evolution; **Amazon Athena** (and/or Spark) for SQL over Iceberg.
- **Ingestion:** the existing SQS consumer writes to Iceberg (directly or via a
  streaming/compaction job) instead of `INSERT`-ing into PostgreSQL.
- **Serving:** the HTTP API and `DashboardSummary` semantics stay **identical**;
  only the repository implementation behind `MetricsRepository` changes.

### Continuous validation (reconciliation)

The migration is de-risked by a reconciliation check that asserts the
**old (PostgreSQL) and new (Iceberg) stores agree** for the same event set:

1. Seed / replay a fixed event set into both stores.
2. Compare every analytics response (`DashboardSummary`, `getUserActivity`,
   `getDocumentStats`, `getTopContent`, `getActiveUsers`, `getStorageUsage`,
   `getExportData`, event counts) field-for-field.
3. Cross-check the persisted daily rollup against counts derived from the raw
   event log.

The baseline for (2)–(3) already exists as
`src/test/scala/com/otterworks/analytics/repository/PostgresMetricsRepositorySpec.scala`,
which proves the durable PostgreSQL store reconciles exactly with the in-memory
store. The lakehouse "after" is expected to pass the **same** reconciliation
against this PostgreSQL "before", turning a one-off migration into a
continuously-validated cutover.

## Build & test

```bash
sbt compile        # compile
sbt test           # unit tests + durable-store reconciliation (Testcontainers)
sbt assembly       # fat jar (used by the Docker image / deploy)
```

The reconciliation suite uses Testcontainers and requires Docker; when Docker is
unavailable it is cancelled (not failed), so Docker-less runners stay green.

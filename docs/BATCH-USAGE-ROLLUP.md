# Usage Rollup: Batch Baseline & Event-Driven Pipeline

> **Status: re-architected.** The nightly CronJob has been **decommissioned**;
> production rollups now flow through the event-driven pipeline
> (**EventBridge → SQS (with DLQ) → Lambda incremental upsert**). The batch
> code remains runnable **locally** as the deterministic comparison baseline.

## What it is

`analytics-service` ingests raw analytics events (document views, file uploads,
storage allocations, collaboration sessions, etc.). The **nightly usage-rollup
job** is the classic legacy reporting pattern layered on top of that stream:

1. **Wake up on a fixed schedule** — a Kubernetes `CronJob` fires nightly
   (`0 2 * * *` UTC).
2. **Bulk-load ALL of the day's events synchronously** (poll-and-process). The
   whole batch is read into memory up front — there is no per-event trigger.
3. **Aggregate in a single pass** into one `DailyUsageRollup` per calendar day
   (UTC): total events, distinct active users, per-type counts, and storage
   allocated / released / net bytes.
4. **Write one output document** (`UsageRollupReport`) and exit.

This is intentionally **not** event-driven: nothing reacts to individual events;
work is deferred to a nightly window and processed in one large synchronous
sweep. That latency + batch-window coupling is exactly what the re-architecture
demo removes.

### Code

| Concern | File |
|---------|------|
| Batch entrypoint (main) | `services/analytics-service/.../batch/UsageRollupJob.scala` |
| Pure aggregation logic | `services/analytics-service/.../batch/UsageRollupAggregator.scala` |
| Bulk NDJSON loader | `services/analytics-service/.../batch/EventLoader.scala` |
| Output models | `services/analytics-service/.../model/UsageRollup.scala` |
| Deterministic seed data | `services/analytics-service/src/main/resources/seed/usage-events.ndjson` |
| Seed generator | `services/analytics-service/scripts/generate_seed_events.py` |
| Unit tests | `services/analytics-service/src/test/.../batch/*.scala` |

### Event-driven path (the "after")

| Concern | File |
|---------|------|
| Incremental upsert logic (pure) | `services/analytics-service/.../event/IncrementalUsageRollup.scala` |
| Lambda handler (SQS → upsert) | `services/analytics-service/.../event/UsageRollupLambdaHandler.scala` |
| Rollup store (DynamoDB / in-memory) | `services/analytics-service/.../event/RollupStore.scala` |
| EventBridge rule, SQS + DLQ, Lambda, DynamoDB | `infrastructure/terraform/modules/usage-rollup/` |
| Tests (incremental + handler) | `services/analytics-service/src/test/.../event/*.scala` |

## Run it locally

```bash
# From repo root — runs against the bundled deterministic seed, writes
# rollup-output.json (override the path with OUT=...).
make batch-usage-rollup
# or:
scripts/run-usage-rollup.sh /tmp/usage-rollup.json

# Regenerate the deterministic seed (reproducible byte-for-byte):
make batch-usage-rollup-seed
```

Configuration is via environment variables:

| Variable | Default | Meaning |
|----------|---------|---------|
| `ROLLUP_INPUT` | `/seed/usage-events.ndjson` | NDJSON events source: a filesystem path, else a classpath resource |
| `ROLLUP_OUTPUT` | `rollup-output.json` | Output JSON path |

Against the bundled seed (165 events across 2024-03-01…03), the job produces
three identical daily rollups (55 events/day, 8 active users, 6 MiB allocated /
2 MiB released / 4 MiB net) — deterministic output suitable for assertions.

## Deployment

The Kubernetes `CronJob` (`templates/cronjob.yaml`, schedule `0 2 * * *`) has
been removed from the `analytics-service` Helm chart. The event path deploys
via Terraform:

```bash
cd services/analytics-service && sbt assembly   # builds the Lambda fat jar
cd infrastructure/terraform && terraform apply  # module "usage_rollup"
```

The Lambda handler is
`com.otterworks.analytics.event.UsageRollupLambdaHandler::handleRequest`
(runtime `java17`), consuming the SQS queue in batches of 10 and upserting one
DynamoDB item per calendar date (`otterworks-usage-rollups-<env>`). Messages
that fail 3 deliveries land on the dead-letter queue.

## The re-architecture

The batch job coupled reporting to a nightly window and reprocessed everything
in bulk. The event-driven pipeline removes that latency:

```
                 (today: batch)                         (target: event-driven)

  analytics events                              analytics event
        │                                              │  emits domain event
        ▼                                              ▼
  [ nightly CronJob ]                          [ Amazon EventBridge rule ]
        │  bulk read all events                       │  routes matching events
        ▼                                              ▼
  aggregate in one pass                        [ Amazon SQS queue ]  (buffer/retry/DLQ)
        │                                              │  triggers
        ▼                                              ▼
  write daily rollup                           [ AWS Lambda ]  incremental rollup upsert
                                                       │
                                                       ▼
                                               rollups updated continuously
```

How it maps:

- **EventBridge rule** — `source = otterworks.analytics`,
  `detail-type = AnalyticsEvent` events are routed to the **SQS** queue
  (buffering, retries, dead-letter queue after 3 failed deliveries).
- **Lambda** — consumes SQS in batches of 10 and performs an **incremental**
  rollup upsert keyed on the UTC calendar date in DynamoDB, so rollups are
  fresh within seconds instead of up to 24 h stale. Distinct `activeUsers` are
  kept exact by persisting the per-day user-id set; storage
  allocated/released/net bytes follow the same metadata parsing as the batch
  aggregator.
- **CronJob decommissioned** — removed from the Helm chart; the batch path
  stays runnable locally (`make batch-usage-rollup`) as the comparison
  baseline. Folding the seed events through the incremental path reproduces
  the batch output exactly (see `IncrementalUsageRollupSpec` /
  `UsageRollupLambdaHandlerSpec`).

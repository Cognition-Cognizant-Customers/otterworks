# OtterWorks ETL Migration Guide: Legacy Cron Scripts to Apache Airflow

## Current State (Legacy)

The OtterWorks ETL pipeline consists of five Python scripts executed via system cron on a single EC2 instance. The scripts share a `config.ini` file that contains hardcoded AWS credentials, database passwords, and service URLs in plaintext.

### Problems with the Current Implementation

| Problem | Impact |
|---------|--------|
| **Hardcoded credentials in `config.ini`** | Security risk; credentials committed to version control |
| **No orchestration** | Cron provides no dependency management, no DAG visibility, no backfill capability |
| **No retry logic** | Transient AWS/network failures cause silent data loss |
| **`print()` logging** | No structured logging, no log levels, no correlation IDs; logs go to flat files on disk |
| **Silent `except: pass` blocks** | Errors are swallowed; data quality issues go undetected for days |
| **No connection reuse** | Each script creates fresh boto3 clients and psycopg2 connections inline |
| **Monolithic `main()` functions** | No separation of extract/transform/load; impossible to test individual stages |
| **No tests** | Zero test coverage; changes are deployed by prayer |
| **Pandas for aggregation** | Loads entire datasets into memory; will not scale beyond a few GB |
| **No alerting** | Failures only discovered when someone manually checks `/var/log/etl/` |
| **Manual cron management** | Schedule changes require SSH access to the production server |
| **No idempotency** | Re-running a script may duplicate data or fail on conflicts |

### Known Limitations Backlog

The legacy scripts used to carry these items as inline `TODO` comments; they are tracked
here instead so the backlog lives in one place and maps onto the migration axes below.

| Ticket | Script | Limitation | Migration axis |
|--------|--------|------------|----------------|
| ETL-078 | `analytics_daily.py` | Monolithic `main()`, no module boundaries | 1, 8 |
| ETL-089 | `analytics_daily.py` | SQS queue URL was hardcoded | **Done** — now `[sqs] analytics_queue_url` in `config.ini`; moves to an Airflow Variable in axis 2 |
| ETL-103 | `analytics_daily.py` | No dead-letter queue for failed/malformed SQS messages | 9 |
| ETL-142 | `analytics_daily.py` | Credentials in `config.ini` rather than a secrets manager | 2 |
| ETL-155 | `analytics_daily.py` | Pandas in-memory aggregation does not scale | 5 |
| ETL-201 | `analytics_daily.py` | No unit tests | 8 |
| ETL-134 | `audit_archive_weekly.py` | Full DynamoDB scan instead of incremental archival | 1 |
| ETL-167 | `audit_archive_weekly.py` | DynamoDB throttling / partial batch failures not retried | 7 |
| ETL-199 | `audit_archive_weekly.py` | No tests | 8 |
| ETL-112 | `search_reindex_weekly.py` | No retry logic for transient API failures | 7 |
| ETL-145 | `search_reindex_weekly.py` | No HTTP connection pooling | 3 |
| ETL-188 | `search_reindex_weekly.py` | No request timeouts | 7 |
| ETL-091 | `storage_cleanup_daily.py` | Manual cleanup instead of S3 lifecycle rules | 1 |
| ETL-156 | `storage_cleanup_daily.py` | Serial S3 listing for large buckets | 1 |
| ETL-203 | `storage_cleanup_daily.py` | No dry-run mode | 8 |
| ETL-098 | `user_activity_daily.py` | Unranged S3 reads; missing days were skipped silently | Partially done — missing days are now logged; range requests remain (axis 3) |
| ETL-160 | `user_activity_daily.py` | PostgreSQL connection not reused across runs | 4 |
| ETL-210 | `user_activity_daily.py` | No email notification on report generation | 9 |

### Script Inventory

| Script | Schedule | Description |
|--------|----------|-------------|
| `analytics_daily.py` | Daily 02:00 UTC | Extract SQS + DynamoDB events, aggregate by user/document/file/hour, load to S3 (gzip JSON) and PostgreSQL |
| `audit_archive_weekly.py` | Sunday 03:00 UTC | Scan DynamoDB for events older than 90 days, compress to JSONL.gz, upload to S3 Glacier, batch-delete from DynamoDB |
| `search_reindex_weekly.py` | Sunday 04:00 UTC | Clear MeiliSearch indices, paginate document-service and file-service APIs, bulk index, validate counts |
| `storage_cleanup_daily.py` | Daily 02:30 UTC | List S3 objects, compare with DynamoDB metadata, quarantine orphaned files, generate savings report |
| `user_activity_daily.py` | Daily 05:00 UTC | Query PostgreSQL aggregates, read per-user S3 data, generate activity reports for admin-service |

---

## Target State

### Technology Stack

| Component | Current (Legacy) | Target |
|-----------|-----------------|--------|
| Orchestration | System cron | **Apache Airflow 2.8+** |
| Configuration | `config.ini` with plaintext secrets | **Airflow Variables + Connections** |
| AWS Access | Raw `boto3` with inline credentials | **Airflow Amazon Provider Hooks** (S3Hook, SqsHook, DynamoDBHook) |
| Database Access | Raw `psycopg2` with manual connect/close | **Airflow PostgresHook** with connection pooling |
| Aggregation | `pandas` in-memory | **PySpark** on EMR/Spark cluster |
| Logging | `print()` statements | **Python `logging` module** with structured output |
| Error Handling | `try: except: pass` | **Airflow retry policies** with exponential backoff |
| Testing | None | **pytest** suite with unit and integration tests |
| Alerting | None (check log files manually) | **Airflow alerting** (email, Slack, PagerDuty callbacks) |

### Architecture Overview

```
                    +-------------------+
                    |   Airflow 2.8+    |
                    |   (Scheduler +    |
                    |    Web UI)        |
                    +--------+----------+
                             |
              +--------------+--------------+
              |              |              |
     +--------v---+  +------v-----+  +-----v------+
     | DAG:        |  | DAG:       |  | DAG:       |
     | analytics   |  | audit      |  | search     |  ...
     | _etl        |  | _archive   |  | _reindex   |
     +--------+----+  +------+-----+  +-----+------+
              |              |              |
     +--------v--------------v--------------v------+
     |          Airflow Provider Hooks              |
     |  S3Hook | SqsHook | DynamoDBHook |          |
     |  PostgresHook | Custom Operators             |
     +--------+--------+---------+---------+-------+
              |        |         |         |
         +----v--+ +---v---+ +--v----+ +--v--------+
         |  SQS  | |DynamoDB| |  S3   | | PostgreSQL|
         +-------+ +-------+ +-------+ +-----------+
```

---

## Migration Axes

### 1. Cron to Airflow DAGs

**Current:** Five independent cron entries in `/etc/crontab` with no dependency management.

**Target:** Five Airflow DAGs with explicit task dependencies, schedule intervals, catchup/backfill support, and the Airflow web UI for monitoring and manual triggering.

**Key changes:**
- Each script becomes a DAG with multiple `PythonOperator` tasks (extract, transform, load, report)
- Dependencies between tasks are expressed as `task_a >> task_b`
- Parallel branches where tasks are independent (e.g., extract from SQS and DynamoDB simultaneously)
- `max_active_runs=1` to prevent overlapping executions

### 2. `config.ini` to Airflow Variables + Connections

**Current:** All configuration in a single `config.ini` file, including AWS credentials and database passwords in plaintext.

**Target:**
- AWS credentials managed via Airflow Connections (`aws_default`) backed by IAM roles or a secrets backend (AWS Secrets Manager)
- Database credentials in an Airflow Connection (`otterworks_postgres`)
- Non-sensitive configuration (bucket names, queue URLs, service URLs) in Airflow Variables
- No credentials in version control

### 3. Raw `boto3` to Airflow Provider Hooks

**Current:** Each script creates raw `boto3.client()` and `boto3.resource()` instances with explicit access keys.

**Target:**
- `S3Hook(aws_conn_id="aws_default")` for all S3 operations
- `SqsHook(aws_conn_id="aws_default")` for SQS polling
- `DynamoDBHook(aws_conn_id="aws_default")` for DynamoDB scans
- Hooks manage credential injection, connection reuse, and region configuration automatically

### 4. Raw `psycopg2` to PostgresHook

**Current:** Manual `psycopg2.connect()` / `cursor()` / `close()` with no connection pooling, no context managers, and error-prone manual cleanup.

**Target:**
- `PostgresHook(postgres_conn_id="otterworks_postgres")` with built-in connection pooling
- Use `pg_hook.get_records()` for SELECT queries
- Use `pg_hook.run()` for INSERT/UPDATE/DELETE
- Automatic connection lifecycle management

### 5. Pandas to PySpark

**Current:** `analytics_daily.py` uses `pandas` to load all events into memory and iterate row-by-row for aggregation.

**Target:**
- PySpark jobs submitted to an EMR or Spark cluster via `SparkSubmitOperator`
- Distributed aggregation using `groupBy().agg()` with typed columns
- Partitioned Parquet output for downstream Athena/Presto queries
- Schema validation via `StructType` definitions

### 6. `print()` to Structured Logging

**Current:** All scripts use `print()` with timestamp-prefixed strings. Log output goes to flat files on disk.

**Target:**
- Python `logging` module with named loggers (`logging.getLogger(__name__)`)
- Structured log output compatible with CloudWatch, Datadog, or ELK
- Log levels (DEBUG, INFO, WARNING, ERROR) for filtering
- Airflow task logs accessible from the web UI

### 7. No Retry to Airflow Retry Policies

**Current:** No retry logic anywhere. Transient failures (network timeouts, AWS throttling, database locks) cause immediate script failure or silent data loss via `except: pass`.

**Target:**
- DAG-level `default_args` with `retries=2` or `retries=3` and `retry_delay=timedelta(minutes=5)`
- Exponential backoff for AWS API calls via provider hooks
- Task-level retry configuration for particularly flaky operations
- `email_on_failure=True` for automatic failure notification

### 8. No Tests to pytest Suite

**Current:** Zero test coverage. The scripts have never been tested in isolation.

**Target:**
- Unit tests for transform/aggregation logic (extracted into testable pure functions)
- Integration tests with mocked AWS services (moto, LocalStack)
- Airflow DAG validation tests (import checks, task dependency validation)
- pytest fixtures for common test data and mock configurations
- CI/CD pipeline running tests on every pull request

### 9. Silent Failures to Airflow Alerting

**Current:** `try: except: pass` blocks silently swallow errors. The only way to discover failures is to SSH into the server and grep through `/var/log/etl/`.

**Target:**
- Airflow's built-in email alerting on task failure
- SLA monitoring for tasks that take too long
- Custom alerting callbacks (Slack, PagerDuty) via `on_failure_callback`
- Dead-letter queues for malformed SQS messages instead of silent drops

---

## Script-to-DAG Mapping

| Legacy Script | Target DAG | Schedule | Tasks |
|---------------|-----------|----------|-------|
| `analytics_daily.py` | `otterworks_analytics_etl` | `@daily` | `extract_from_sqs` -> `extract_from_dynamodb` -> `transform_events` -> `[load_to_data_lake, update_postgres_aggregates]` -> `generate_report` |
| `audit_archive_weekly.py` | `otterworks_audit_archive` | `@weekly` | `scan_audit_events` -> `compress_and_upload` -> `cleanup_dynamodb` -> `generate_compliance_report` |
| `search_reindex_weekly.py` | `otterworks_search_reindex` | `@weekly` | `clear_indices` -> `[fetch_and_index_documents, fetch_and_index_files]` -> `validate_indices` |
| `storage_cleanup_daily.py` | `otterworks_storage_cleanup` | `@daily` | `[list_s3_objects, list_metadata_references]` -> `find_orphaned_objects` -> `move_to_quarantine` -> `generate_storage_report` |
| `user_activity_daily.py` | `otterworks_user_activity_report` | `@daily` | `[query_analytics_aggregates, query_per_user_activity]` -> `generate_user_reports` -> `store_reports_to_s3` |

---

## Migration Steps (Recommended Order)

1. **Set up Airflow infrastructure** -- Deploy Airflow 2.8+ with CeleryExecutor or KubernetesExecutor. Configure the webserver, scheduler, and worker(s).

2. **Configure Connections and Variables** -- Create Airflow Connections for AWS (`aws_default`) and PostgreSQL (`otterworks_postgres`). Migrate all `config.ini` values to Airflow Variables.

3. **Migrate `analytics_daily.py` first** -- This is the most complex script and touches the most systems. Successful migration validates the patterns for all other scripts.

4. **Add PySpark aggregation** -- Replace the pandas-based aggregation in analytics with a PySpark job for scalability.

5. **Migrate remaining scripts** -- Apply the same patterns established in step 3 to the other four scripts.

6. **Add test suite** -- Write pytest tests for all transform/aggregation logic. Add DAG validation tests.

7. **Enable alerting** -- Configure email notifications, SLA monitoring, and Slack/PagerDuty callbacks.

8. **Decommission cron** -- Remove cron entries from the EC2 instance. Archive the legacy scripts.

9. **Delete `config.ini`** -- Remove the plaintext credentials file from the repository and rotate all exposed secrets.

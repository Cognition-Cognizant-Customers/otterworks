# OtterWorks Serverless ETL

AWS serverless replacement for the legacy cron-based ETL in `etl/`. Each of the five
pipelines runs as a Step Functions state machine invoking a per-pipeline Lambda
(container image built from this directory), scheduled by EventBridge Scheduler.

## Pipelines

| Pipeline | Schedule (UTC) | State machine flow |
|----------|----------------|--------------------|
| analytics | daily 02:00 | extract_from_sqs ∥ extract_from_dynamodb → transform_events → load_to_data_lake ∥ update_postgres_aggregates → generate_report |
| audit-archive | Sun 03:00 | scan_audit_events → (skip if empty) compress_and_upload → cleanup_dynamodb → generate_compliance_report |
| search-reindex | Sun 04:00 | clear_indices → fetch_and_index_documents ∥ fetch_and_index_files → validate_indices |
| storage-cleanup | daily 02:30 | list_s3_objects ∥ list_metadata_references → find_orphaned_objects → move_to_quarantine → generate_storage_report |
| user-activity | daily 05:00 | query_analytics_aggregates ∥ query_per_user_activity → generate_user_reports → store_reports_to_s3 |

## Key design points

- **No plaintext credentials**: AWS access via Lambda execution roles (least-privilege,
  per pipeline); PostgreSQL and MeiliSearch credentials in Secrets Manager
  (`DB_SECRET_ID` / `MEILISEARCH_SECRET_ID`). Legacy `etl/config.ini` is not used.
- **S3 staging**: intermediate task outputs are gzipped JSON under
  `etl-staging/{pipeline}/{execution_id}/` in the data-lake bucket, keeping Step
  Functions payloads small.
- **Retries + alerting**: every Lambda task retries with exponential backoff; failures
  publish to an SNS alerts topic. Malformed analytics SQS messages go to a DLQ.
- **Structured logging**: single-line JSON to stdout for CloudWatch Logs Insights.
- **Idempotent loads**: PostgreSQL upserts (`ON CONFLICT ... DO UPDATE`) keyed on
  report date, and the analytics SQS extract stages consumed events under the
  report date (not the execution id), so a re-run for the same day reuses the
  already-drained queue events and recomputes the full day instead of
  overwriting it with partial numbers. The state machine additionally skips the
  load/report states when a run finds no events at all.

## Layout

- `src/otterworks_etl/common/` — config/secrets, boto3 clients, JSON logging, S3
  staging, psycopg2 connection helper, task dispatcher.
- `src/otterworks_etl/<pipeline>/handler.py` — Lambda tasks; `transform.py` holds pure
  logic where applicable.
- `Dockerfile` — Lambda Python 3.12 container image; Terraform overrides the image
  `command` per function.
- Infrastructure: `infrastructure/terraform/modules/etl/` (Lambdas, state machines,
  schedules, IAM, secrets, SNS, DLQ, quarantine bucket), wired in the root module
  behind `etl_image_uri` (empty = not provisioned).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=src .venv/bin/python -m pytest tests
.venv/bin/python -m ruff check src tests
```

## Deploying

1. Build and push the image: `docker build -t <ecr-repo>:<tag> etl-serverless/ && docker push ...`
2. `terraform apply -var etl_image_uri=<ecr-repo>:<tag>` in `infrastructure/terraform`.
3. Populate the two Secrets Manager secrets created by the module (database and
   MeiliSearch) out-of-band — values are never stored in Terraform.
4. Decommission the legacy EC2 cron box once parallel runs check out, and rotate all
   credentials that were present in `etl/config.ini`.

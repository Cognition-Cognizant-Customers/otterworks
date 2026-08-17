# Shared platform skeletons

These roots contain only scaffolding shared by both units on each platform:

- `infrastructure/databricks/cronbox/` — Unity Catalog bootstrap and paused
  serverless SQL job shells for cron-analytics and cron-activity.
- `infrastructure/terraform/tp-cronbox/` — three prefixed S3 buckets and
  three PAY_PER_REQUEST DynamoDB tables, with local Terraform state.
- `scripts/tp_atlas/cronbox_namespace.py` — idempotent `ow_tp_demo` database
  and `documents`/`files` collection bootstrap on the existing M0 cluster.

Unit-owned notebooks, Lambda functions, EventBridge rules, lifecycle rules,
and Atlas Search indexes remain with the children. The parent owns all live
applies; `make tp-skeleton-validate` is offline/read-only validation.

The Databricks bundle targets the existing serverless SQL warehouse discovered
as `Serverless Starter Warehouse` (`565cd2fd713738c4`) in the configured
workspace. Confirm that warehouse before deployment if workspace state has
changed. Atlas cluster identity was not queried or modified; the skeleton
assumes the configured `MONGODB_ATLAS_URI` points at the existing free-tier M0.

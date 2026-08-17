# Cron Box AWS shared skeleton

This is a separate, local-state Terraform root for the shared AWS resources
used by the Cron Box archive and cleanup units. It creates only three S3
buckets and three PAY_PER_REQUEST DynamoDB tables in `us-east-1`. Every
taggable resource receives `Project=otterworks-tp` through provider
`default_tags`; names use the `ow-tp-` prefix.

Beyond that skeleton, each unit adds its own file. Nothing is scheduled, and no
resource carries an hourly cost.

## `audit_archive.tf` — cron-archive unit

Replaces `etl/scripts/audit_archive_weekly.py` (weekly cron) with expiry-driven
configuration: DynamoDB TTL on `ow-tp-audit-events` (`expires_at`, 90 days) plus
a stream (`NEW_AND_OLD_IMAGES`) whose TTL-attributed `REMOVE` events invoke
`ow-tp-audit-archive`, which writes one JSONL.gz object per event under
`s3://ow-tp-audit-archive/audit-archive/expired/` before the expiry loses it. An
S3 lifecycle rule transitions that prefix to `GLACIER`; failed batches land in
`ow-tp-audit-archive-dlq`. The same function serves an on-demand
`{"mode": "sweep"}` reconciliation invoke, so items expiring inside TTL's
best-effort window (up to ~48h) are archived without waiting for the deletion.

Live recon (parent-run, read-only apart from its own prefix, self-cleaning):

```sh
python3 scripts/tp_aws/audit_archive_recon.py --mode live \
  --out docs/tech-partnerships/recon/cron-archive-demo.recon.json
make tp-validate-recon FILE=docs/tech-partnerships/recon/cron-archive-demo.recon.json
```

The committed `*.fixture.recon.json` is the child's LocalStack run of the same
script (`--mode fixture`, isolated `-fixture` table/bucket).

Offline validation:

```sh
terraform init -backend=false
terraform validate
terraform fmt -check
```

The parent owns the apply. Do not run `terraform apply` or `terraform destroy`
from a child session.

## Cron cleanup (parent-applied only)

The cron-cleanup unit adds the event-driven `ow-tp-orphan-quarantine` Lambda,
its EventBridge S3 object-created rule, DLQ, IAM policy, and quarantine
lifecycle rule. It has no schedule, provisioned concurrency, or other hourly
cost resource. The parent applies this root; child sessions only validate it.

The handler also exposes an on-demand `{"mode": "reconcile"}` sweep for manual
reconciliation. A production variant would use an SQS delay queue for the
metadata write-order recheck rather than waiting inside Lambda. This bounded
in-invocation wait keeps the resource set to the contract's list.

Each event still performs a full metadata scan, mirroring the legacy daily
scan. A future `s3_key` GSI is the follow-up optimization and belongs to the
parent because it would alter the shared metadata table.

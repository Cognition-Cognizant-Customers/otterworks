# Cron Box AWS shared skeleton

This is a separate, local-state Terraform root for the shared AWS resources
used by the Cron Box archive and cleanup units. It creates only three S3
buckets and three PAY_PER_REQUEST DynamoDB tables in `us-east-1`. Every
taggable resource receives `Project=otterworks-tp` through provider
`default_tags`; names use the `ow-tp-` prefix.

No compute, network, database, provisioned capacity, lifecycle rule, event
rule, Lambda, IAM role, stream, or alarm is defined here. Those are owned by
the relevant child unit.

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

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

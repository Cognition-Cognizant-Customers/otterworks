# terraform-tp-aws — AWS serverless CUSTBILL pipeline (tech-partnerships)

Self-contained Terraform stack that replaces the legacy pet-box CUSTBILL batch chain
(`etl/legacy-extra/`) with an event-driven serverless pipeline. Separate from
`infrastructure/terraform` (different state, no shared resources), and safe to
`terraform destroy` at any time.

```
s3://ow-tp-ingest-<account>/landing/<ns>/CUSTBILL_*.dat
  -> EventBridge "Object Created"        (rule ow-tp-landing-object-created)
    -> SQS ow-tp-ingest-queue (+ -dlq)
      -> Lambda ow-tp-trigger            replaces etl/legacy-extra/jobs/sftp_ingest_poll.ksh
        -> Step Functions ow-tp-custbill-pipeline   replaces run_all.sh sleep-sequencing
             Parse  : Lambda ow-tp-parse  replaces jobs/parse_custbill_fixedwidth.sh
                      -> parsed/<ns>/<file>.psv + DynamoDB ow-tp-billing-records
             Report : Lambda ow-tp-report replaces jobs/finance_excel_report.pl
                      -> reports/<ns>/finance_billing_<stamp>.csv (+ .xls copy)
```

## Layout

| File | Owner | Contents |
|---|---|---|
| `main.tf`, `iam.tf`, `variables.tf`, `outputs.tf`, `versions.tf` | shared skeleton | S3 bucket, EventBridge rule, SQS + DLQ, DynamoDB, Lambda packaging, naming/ARN locals, assume-role documents |
| `component-ingest.tf` | ingest component | `ow-tp-trigger` Lambda + SQS event-source mapping |
| `component-parse.tf` | parse component | `ow-tp-parse` Lambda + its IAM policy |
| `component-report.tf` | report/orchestration component | `ow-tp-report` Lambda + `ow-tp-custbill-pipeline` state machine |

Components never reference each other's Terraform resources — they use the
deterministic names/ARNs in `local.lambda_names`, `local.lambda_arns` and
`local.state_machine_arn` so each component can be developed and merged on its own.
Per-component contracts: `docs/tech-partnerships/contracts/aws-serverless-*.md`.

## Usage

```bash
# the stack's region comes from var.aws_region (default us-east-1); set it with
# TF_VAR_aws_region, not AWS_DEFAULT_REGION — the helper scripts read the
# deployed region back out of the stack outputs.
export TF_VAR_aws_region=us-east-1
make aws-tp-apply                 # terraform init + apply
make legacy-etl-gen-data NS=demo  # deterministic CUSTBILL input
make aws-tp-run NS=demo           # upload the sample files to landing/<ns>/
make aws-tp-verify NS=demo        # recon vs. the legacy golden outputs
make aws-tp-destroy               # destroy + tag scan proving nothing is left
```

## Cost discipline

Serverless / on-demand only: S3, Lambda, SQS, EventBridge, Step Functions (Standard),
DynamoDB `PAY_PER_REQUEST`, CloudWatch log groups with 7-day retention. No EC2, NAT
gateways, RDS, or load balancers — nothing with an hourly charge. Every resource is
named `ow-tp-*` and tagged `Project=otterworks-tp` (provider `default_tags`), so
`make aws-tp-destroy` can prove teardown with a tag scan.

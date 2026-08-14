# terraform-tp-aws — serverless "after" state for the AWS partner demo

Self-contained Terraform stack (separate **local** state, nothing shared with
`infrastructure/terraform/`) that replaces the legacy CUSTBILL pet-box chain
(`etl/legacy-extra/`) with an event-driven serverless pipeline:

```
s3://ow-tp-ingest-<acct>/landing/<ns>/CUSTBILL_*.dat
  -> EventBridge rule (Object Created, prefix landing/)
    -> SQS ow-tp-ingest-queue (DLQ: ow-tp-ingest-dlq, maxReceive 3)
      -> Lambda ow-tp-trigger
        -> Step Functions ow-tp-custbill-pipeline
             ParseCustbill  (ow-tp-parse):  fixed-width -> parsed/<ns>/*.psv + DynamoDB ow-tp-billing-records
             FinanceReport  (ow-tp-report): reports/<ns>/finance_billing_<YYYYMMDD>.csv (+ .xls copy)
```

Lambda source: `services/serverless-ingest/` (semantically equivalent to
`parse_custbill_fixedwidth.sh` + `finance_excel_report.pl`, parameterized by
namespace for multi-tenant fan-out).

Cost discipline: Lambda, Step Functions, EventBridge, SQS, DynamoDB
**on-demand**, S3 only — nothing with an hourly cost. Every resource is tagged
`Project=otterworks-tp` and named `ow-tp-*`.

## Apply

```bash
cd infrastructure/terraform-tp-aws
terraform init
terraform apply -auto-approve      # ~2 minutes
```

Credentials: standard AWS env vars (`AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY`, region `us-east-1` by default).

## Verify (recon vs. the legacy chain)

From the repo root:

```bash
make aws-tp-verify NS=dev
```

Seeds deterministic sample input (`etl/legacy-extra/tools/gen_sample_data.pl`),
runs the legacy chain locally, uploads the same files to `landing/<ns>/`, waits
for the pipeline, and diffs the serverless outputs (parsed `.psv` files and the
finance report) against the legacy outputs byte-for-byte. Writes a recon report
and exits non-zero on any mismatch.

## Destroy

```bash
terraform destroy -auto-approve
```

Removes everything, including the bucket contents (`force_destroy = true`) and
the CloudWatch log groups. Confirm no leftovers:

```bash
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=otterworks-tp \
  --query 'ResourceTagMappingList[].ResourceARN'
```

The tagging API is eventually consistent: a just-deleted Lambda event-source
mapping can linger in its listing for a while. If an ARN shows up, confirm the
resource is actually gone (e.g. `aws lambda get-event-source-mapping --uuid
<uuid>` returns `ResourceNotFoundException`).

State is local (`terraform.tfstate`, gitignored). The stack is kept destroyed
between demos and re-applied at demo time.

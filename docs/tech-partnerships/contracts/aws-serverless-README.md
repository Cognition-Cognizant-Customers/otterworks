# AWS serverless CUSTBILL migration — component contracts

The legacy chain (`etl/legacy-extra/`) is replaced component-by-component by an
event-driven serverless pipeline in `infrastructure/terraform-tp-aws` +
`services/serverless-ingest`. Each component is built independently against the
contract in its own file, then merged bottom-up.

| Component | Contract | Replaces (legacy) | Terraform file | Handler |
|---|---|---|---|---|
| ingest | [`aws-serverless-ingest.md`](aws-serverless-ingest.md) | `jobs/sftp_ingest_poll.ksh` | `component-ingest.tf` | `src/handler_trigger.py` |
| parse | [`aws-serverless-parse.md`](aws-serverless-parse.md) | `jobs/parse_custbill_fixedwidth.sh` | `component-parse.tf` | `src/handler_parse.py` |
| report + orchestration | [`aws-serverless-report.md`](aws-serverless-report.md) | `jobs/finance_excel_report.pl` + `run_all.sh` | `component-report.tf` | `src/handler_report.py` |

## Shared substrate (already applied, do not modify)

The skeleton in `infrastructure/terraform-tp-aws/{main,iam,variables,outputs,versions}.tf`
owns the S3 bucket, EventBridge rule, SQS queue + DLQ, DynamoDB table, Lambda
packaging and the naming locals. Components consume it and never edit it:

| Need | Use |
|---|---|
| bucket name | `aws_s3_bucket.ingest.bucket` (output `ingest_bucket`) |
| table name | `aws_dynamodb_table.billing.name` (`ns` hash key, `rec` range key) |
| queue / DLQ | `aws_sqs_queue.ingest`, `aws_sqs_queue.ingest_dlq` |
| Lambda zip | `data.archive_file.lambda` (`source_code_hash = ...output_base64sha256`) |
| function names / ARNs | `local.lambda_names["trigger"|"parse"|"report"]`, `local.lambda_arns[...]` |
| state machine | `local.state_machine_name`, `local.state_machine_arn` |
| handler env | `local.lambda_env` (`BUCKET`, `TABLE_NAME`, `STATE_MACHINE_ARN`, `TZ`) |
| IAM trust | `data.aws_iam_policy_document.lambda_assume` / `.sfn_assume`, `local.lambda_basic_execution_policy_arn` |

**Cross-component rule:** never reference another component's Terraform resource
(their file does not exist on your branch). Use the deterministic
`local.lambda_arns` / `local.state_machine_arn` strings.

## Event-shape contract between stages

1. **EventBridge → SQS**: raw S3 `Object Created` event; body is the EventBridge
   envelope, `detail.bucket.name` and `detail.object.key`
   (`landing/<ns>/CUSTBILL_<NS>_<nnn>.dat`).
2. **trigger Lambda → Step Functions** — the pipeline input, one execution per file:

```json
{
  "ns": "demo",
  "bucket": "ow-tp-ingest-599083837640",
  "key": "landing/demo/CUSTBILL_DEMO_001.dat",
  "filename": "CUSTBILL_DEMO_001.dat"
}
```

3. **Parse state result** (`$.parse`):

```json
{
  "ns": "demo",
  "parsed_key": "parsed/demo/CUSTBILL_DEMO_001.psv",
  "records": 50,
  "trailer_count": 50,
  "trailer_match": true
}
```

4. **Report state result** (`$.report`):

```json
{
  "ns": "demo",
  "report_key": "reports/demo/finance_billing_20260815.csv",
  "xls_key": "reports/demo/finance_billing_20260815.xls",
  "rows": 6,
  "files_aggregated": 2
}
```

The report state re-aggregates every `parsed/<ns>/*.psv` object on each execution, so
it is idempotent and order-independent (unlike the legacy `run_all.sh`, which just
slept and hoped the previous stage was done).

## Golden outputs (recon baselines)

Generated with `make legacy-etl-gen-data NS=demo && RUN_ALL_SLEEP=0 make legacy-etl-run JOB=run_all`:

| Golden | Path | sha256 |
|---|---|---|
| parsed file 1 | `/tmp/otterworks-legacy/parsed/CUSTBILL_DEMO_001.psv` | `7fc03e8ceb88ce807b18e3e0a8bb2450b7677108495bdcb883881887c09665bf` |
| parsed file 2 | `/tmp/otterworks-legacy/parsed/CUSTBILL_DEMO_002.psv` | `b576ad3de53b835643dc9096781cb491e6a03b3712c675c5598ab05f8c3c54a3` |
| finance report | `/tmp/otterworks-legacy/reports/finance_billing_<stamp>.csv` | `c8923a71ab5a2d8048ad06ae91840631c009551e9082755fa4672e034a15627e` |

Report body for `NS=demo` (100 records over 2 files):

```csv
Currency,RecordType,RecordCount,TotalAmount
EUR,INVOICE,22,101554.41
EUR,CREDIT,6,33375.97
GBP,INVOICE,32,183113.58
GBP,CREDIT,5,28454.59
USD,INVOICE,28,130502.15
USD,CREDIT,7,33390.44
```

`NS` is a free parameter: any namespace reproduces byte-identical input (seeded
LCG in `tools/gen_sample_data.pl`), so recon must be re-derivable from the legacy
chain rather than hard-coded to these hashes.

## Delivery rules (every component)

- Stacked PRs, bottom-up mergeable, base `tech-partnerships` (never `main`):
  1. Terraform component definitions,
  2. handler code + unit tests,
  3. verification evidence (`make aws-tp-verify NS=demo` slice for the component).
- `make tp-smoke` must pass; the golden app path (`make up` / `make test`) stays untouched.
- Serverless/on-demand only. No EC2/NAT/RDS/ALB, nothing untagged, everything `ow-tp-` prefixed.
- Never create AWS resources from Kubernetes (`AGENTS.md`).
- Do not run `terraform apply` on the shared skeleton, and do not `terraform destroy`
  the stack — the parent session owns the estate lifecycle. `terraform validate` /
  `terraform plan` and direct `aws` CLI calls against your own component are fine.

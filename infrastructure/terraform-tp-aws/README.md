# AWS serverless track — shared Terraform skeleton (parent-owned)

Replaces the legacy CUSTBILL batch chain (`etl/legacy-extra/`) with an
event-driven serverless pipeline: S3 + EventBridge + Lambda + SQS (with a
dead-letter queue) + Step Functions + on-demand DynamoDB. Everything is
Terraform-managed in this single self-contained stack (local state, never
committed), and only the **parent** orchestration session runs
`terraform plan/apply/destroy`.

## Layout

| Stage prefix | Replaces |
|---|---|
| `landing/` | the mainframe SFTP drop dir |
| `incoming/` | `$ROOT/incoming` (staged by ingest) |
| `archive/` | `$ROOT/archive` |
| `parsed/` | `$ROOT/parsed` (`.psv`, byte-identical to legacy) |
| `reports/` | `$ROOT/reports` (`finance_billing_*.csv`/`.xls`) |

Event contract between stages: EventBridge **S3 "Object Created"** events on
the default bus. `landing/` arrivals trigger the ingest component;
`incoming/` arrivals are delivered to the `ow-tp-<ns>-events` SQS queue
(3 receives → `ow-tp-<ns>-events-dlq`) consumed by the parser Lambda via an
event-source mapping. The report + orchestration component owns the
`ow-tp-<ns>-chain` Step Functions state machine, started per **batch**
(matching the legacy `run_all.sh` granularity).

## Rules (apply to every component)

- Components contribute exactly one `<unit>.tf` file to this directory
  (`ingest.tf`, `parser.tf`, `report.tf`) plus function source under
  `services/serverless-ingest/<unit>/`. Never edit the shared files
  (`main.tf`, `variables.tf`, `versions.tf`, `outputs.tf`).
- **Never** run `terraform apply`/`destroy` and never create live cloud
  resources — children self-verify against LocalStack/unit tests and mark
  recon `run_mode: fixture`, listing what only a live apply can prove.
- Names carry the `ow-tp-<ns>-` prefix; tags come from provider
  `default_tags` (`Project=otterworks-tp`). Nothing untagged/unprefixed.
- Serverless / on-demand only: no EC2, NAT gateways, RDS, load balancers, or
  anything else with an hourly cost, even transiently.
- Least privilege is a contract item: use `data.aws_iam_policy_document.lambda_assume`
  for trust; scope S3 writes to the single stage prefix the component owns;
  cross-service trust carries source-account/source-ARN conditions.
- Run `terraform fmt`, `tflint`, and `checkov` on your component file before
  opening the PR; run `make tp-smoke` too.

## Parent drive targets (Makefile)

- `make aws-tp-plan` / `aws-tp-apply` / `aws-tp-destroy` — stack lifecycle.
- `make aws-tp-run NS=<ns>` — land the golden sample inputs and start the
  state machine (one batch).
- `make aws-tp-verify NS=<ns>` — recompute parity from the deployed pipeline
  (byte-diff parsed/reports against the golden baselines, DLQ empty, zero
  failed executions, anomaly set match) and emit a schema-valid recon report.
- `make aws-tp-scan` — negative post-destroy scan by tag and `ow-tp-` prefix.

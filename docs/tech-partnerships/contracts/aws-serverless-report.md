# Component contract — report + orchestration (replaces `finance_excel_report.pl` and `run_all.sh`)

Read [`aws-serverless-README.md`](aws-serverless-README.md) first (shared substrate,
event shapes, delivery rules).

## Legacy behaviour being replaced

`etl/legacy-extra/jobs/finance_excel_report.pl`: reads every `CUSTBILL*.psv` in
`parsed/` (sorted by filename), accumulates `TotalAmount` and `RecordCount` keyed by
`CURRENCY|REC-TYPE`, and writes `reports/finance_billing_<YYYYMMDD>.csv` stamped with
the ETL box's localtime, then `cp`s it to `.xls` because "Excel opens it anyway"
(2004). Rows are emitted in `sort keys` order — i.e. ASCII order of `"<ccy>|<rt>"`, so
`EUR|01, EUR|02, GBP|01, …`. `RecordType` renders as `INVOICE` (01), `CREDIT` (02),
`UNKNOWN(<rt>)` otherwise. Amounts are `%.2f` of a floating-point sum; counts are `%d`.
Header row: `Currency,RecordType,RecordCount,TotalAmount`. It also pipes a mail to
sendmail, which is not installed and silently does nothing — do **not** reimplement the
mail step (note its removal in the PR description).

`etl/legacy-extra/run_all.sh` "orchestrates" by sleeping 600s between stages and
running the next one on partial data if the previous is slow. Step Functions replaces
that with an explicit Parse → Report dependency, retries, and a visible failure state.

Reproduce the report byte-for-byte: same header, same row order, same `%.2f`
formatting, same `UNKNOWN(<rt>)` fallback, and the same date-stamped filename (use the
`TZ` env var, `UTC` by default, so the stamp is deterministic in CI).

## What to build

**`infrastructure/terraform-tp-aws/component-report.tf`**

- `aws_lambda_function` named `local.lambda_names["report"]`, handler
  `handler_report.handler`, `python3.12`, timeout 120, memory 512, zip/hash from
  `data.archive_file.lambda`, env `local.lambda_env`; log group with
  `var.log_retention_days`.
- Role from `data.aws_iam_policy_document.lambda_assume` + basic execution, inline
  policy allowing only `s3:ListBucket` on the bucket ARN, `s3:GetObject`/`s3:PutObject`
  on `"${aws_s3_bucket.ingest.arn}/*"`.
- `aws_sfn_state_machine` named `local.state_machine_name` (must match exactly — the
  trigger Lambda starts it by constructed ARN), role from
  `data.aws_iam_policy_document.sfn_assume` with an inline policy allowing
  `lambda:InvokeFunction` on `local.lambda_arns["parse"]` and `local.lambda_arns["report"]`
  plus the `logs:*LogDelivery`/`logs:PutResourcePolicy`/`logs:DescribeLogGroups` set
  Step Functions requires for logging; `logging_configuration` → a
  `/aws/states/<name>` log group at `ERROR` level with execution data.
  `depends_on` the inline role policy (validated at create time).
- Definition: `ParseCustbill` (Task → `local.lambda_arns["parse"]`, `ResultPath =
  "$.parse"`, retry `States.TaskFailed` ×2 with backoff) → `FinanceReport` (Task →
  `local.lambda_arns["report"]`, `ResultPath = "$.report"`, same retry, `End`).

**`services/serverless-ingest/src/handler_report.py`**

- Keep aggregation pure and unit-testable (`aggregate(psv_lines) -> rows`,
  `render_csv(rows) -> str`).
- Handler input is the Step Functions state (`ns` at the top level, `$.parse` present);
  it lists `parsed/<ns>/` (paginated), sorted by key, aggregates every `.psv`, writes
  `reports/<ns>/finance_billing_<stamp>.csv` and the identical `.xls` copy, and returns
  the Report-state result shape from the README.
- Idempotent: re-aggregates from scratch on every execution, so concurrent
  file-arrival executions converge instead of double-counting (the legacy job's real
  failure mode when two feeds landed in the same window).
- Unit tests: golden report bytes for the `NS=demo` PSV fixtures, `UNKNOWN(<rt>)`
  fallback, empty-parsed-prefix case, row ordering, `.xls` == `.csv` bytes.

**Verification harness — this component also owns it**

- `scripts/aws-tp-verify.sh` (+ `make aws-tp-verify NS=<ns>`, add to `.PHONY`):
  1. run the legacy chain locally for `NS` (or reuse existing golden outputs under
     `$OTTERWORKS_LEGACY_ROOT`) to derive baselines — never hard-code hashes;
  2. download `parsed/<ns>/*.psv` and the newest `reports/<ns>/finance_billing_*.csv`
     from the stack's bucket (`terraform output -raw ingest_bucket`);
  3. compare `sha256` of each `.psv` and of the report CSV; print a per-file PASS/FAIL table;
  4. compare DynamoDB item count for `ns=<ns>` against the legacy record count
     (`aws dynamodb query --select COUNT`);
  5. assert the DLQ is empty and that no Step Functions execution failed;
  6. exit non-zero on any mismatch. Support `--wait <seconds>` polling so it can be
     run right after `make aws-tp-run`.

## Acceptance checks

1. `terraform validate` + `fmt -check` pass; `make tp-smoke` passes.
2. `python -m pytest services/serverless-ingest/tests` passes.
3. Evidence PR shows `make aws-tp-verify NS=demo` green against the applied estate:
   both `.psv` files and the finance report byte-identical to the legacy goldens,
   DynamoDB count 100, DLQ empty, no failed executions.
4. Also demonstrate one failure beat for the ops story: an intentionally malformed
   landing file (e.g. truncated records or a bad trailer) surfaces as a failed
   execution / non-zero verify with a clear message, and the pipeline recovers on the
   next good file. Do not leave the malformed object in `landing/demo/` afterwards.

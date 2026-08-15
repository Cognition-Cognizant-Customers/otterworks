# Component contract — ingest (replaces `sftp_ingest_poll.ksh`)

Read [`aws-serverless-README.md`](aws-serverless-README.md) first (shared substrate,
event shapes, delivery rules).

## Legacy behaviour being replaced

`etl/legacy-extra/jobs/sftp_ingest_poll.ksh`: polls the SFTP drop directory three
times, "settles" each file by comparing `wc -c` twice a second apart, copies it to
`incoming/` and `archive/<name>.<timestamp>`, `rm`s the source, and leaves a lock file
behind forever (incident 2016-03-12). Downstream stages start after a fixed `sleep`
in `run_all.sh`.

Event-driven replacement: S3 `Object Created` on `landing/<ns>/` is the arrival
signal — no polling, no settle hack, no lock file, and the next stage starts because
the file arrived, not because a timer expired.

## What to build

**`infrastructure/terraform-tp-aws/component-ingest.tf`**

- `aws_lambda_function` named `local.lambda_names["trigger"]`, handler
  `handler_trigger.handler`, runtime `python3.12`, timeout 120, memory 256,
  `filename`/`source_code_hash` from `data.archive_file.lambda`,
  `environment.variables = local.lambda_env`.
- `aws_cloudwatch_log_group` `/aws/lambda/<name>` with `var.log_retention_days`
  (so `terraform destroy` removes the logs too).
- `aws_iam_role` from `data.aws_iam_policy_document.lambda_assume` +
  `local.lambda_basic_execution_policy_arn`, plus an inline policy allowing exactly:
  `states:StartExecution` on `local.state_machine_arn`, and
  `sqs:ReceiveMessage|DeleteMessage|GetQueueAttributes` on `aws_sqs_queue.ingest.arn`.
- `aws_lambda_event_source_mapping` from `aws_sqs_queue.ingest.arn`, `batch_size = 10`,
  `function_response_types = ["ReportBatchItemFailures"]`, `depends_on` its inline
  role policy (AWS validates the role at mapping-creation time).

**`services/serverless-ingest/src/handler_trigger.py`**

- Input: SQS batch; each `record["body"]` is the EventBridge envelope.
- For each record: parse bucket/key, ignore keys that are not
  `landing/<ns>/CUSTBILL*.dat` (log and treat as success — no poison-pill retries),
  derive `ns` via `pipeline.namespace_from_key`, and
  `stepfunctions.start_execution(stateMachineArn=..., name=<deterministic-ish>, input=<pipeline input>)`.
- Execution name must be unique per (file, attempt) but re-runnable: e.g.
  `<ns>-<filename sans ext>-<8 hex of md5(key+eventtime)>`, sanitised to `[A-Za-z0-9_-]`,
  ≤80 chars. Treat `ExecutionAlreadyExists` as success (at-least-once delivery).
- Return `{"batchItemFailures": [{"itemIdentifier": <messageId>}, ...]}` for records
  that raised, so only those are retried and eventually land in the DLQ.
- Unit tests (`services/serverless-ingest/tests/`) with a stub Step Functions client:
  happy path, non-CUSTBILL key skipped, malformed body → batch item failure,
  `ExecutionAlreadyExists` swallowed.

## Acceptance checks

1. `terraform -chdir=infrastructure/terraform-tp-aws validate` and `fmt -check` pass.
2. `python -m pytest services/serverless-ingest/tests` passes.
3. `make tp-smoke` passes; golden app path untouched.
4. Evidence PR shows: uploading a CUSTBILL file to `landing/demo/` produces a Step
   Functions execution whose input matches the pipeline-input contract, and the
   queue drains to zero with an empty DLQ
   (`aws sqs get-queue-attributes ... ApproximateNumberOfMessages*`).
   The parent session applies the stack; ask the parent for the applied estate if you
   need a live run before the other components exist (the trigger can be invoked
   directly with a synthetic SQS event).

# Ingest component: replaces etl/legacy-extra/jobs/sftp_ingest_poll.ksh.
#
# EventBridge (aws_cloudwatch_event_rule.landing_object_created) invokes the
# ow-tp-<ns>-ingest Lambda per landed object under landing/; the function
# stages it byte-identically to incoming/, writes a deterministic archive
# copy to archive/, quarantines non-CUSTBILL*.dat keys to quarantine/,
# records batch state in the shared DynamoDB ledger, and deletes the landed
# object. Undeliverable events drain to the shared events DLQ.

# hashicorp/archive is resolved implicitly: the shared versions.tf owns the
# module's single required_providers block and components must not edit it.
# tflint-ignore: terraform_required_providers
data "archive_file" "ingest" {
  type        = "zip"
  source_file = "${path.module}/../../services/serverless-ingest/ingest/handler.py"
  output_path = "${path.module}/.build/ingest.zip"
}

# --- IAM: least-privilege role -------------------------------------------

data "aws_iam_policy_document" "ingest_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name               = "${local.prefix}-ingest-role"
  assume_role_policy = data.aws_iam_policy_document.ingest_assume.json
}

data "aws_iam_policy_document" "ingest" {
  # Read + delete the landed object only.
  statement {
    sid       = "LandingReadDelete"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.pipeline.arn}/landing/*"]
  }

  # ListBucket on the bucket so HeadObject on an already-deleted landed
  # object returns 404 (not 403), which the redelivery no-op path relies on.
  # No s3:prefix condition: HeadObject's 404-vs-403 check carries no s3:prefix
  # in its request context, so a prefix-conditioned Allow never matches it.
  # Read-only; bucket writes stay scoped to the stage prefixes below.
  statement {
    sid       = "LandingList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.pipeline.arn]
  }

  # Write only the stage prefixes this component owns — no bucket-wide write.
  statement {
    sid     = "StageWrite"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.pipeline.arn}/incoming/*",
      "${aws_s3_bucket.pipeline.arn}/archive/*",
      "${aws_s3_bucket.pipeline.arn}/quarantine/*",
    ]
  }

  # Batch-state ledger.
  statement {
    sid       = "BatchStateLedger"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [aws_dynamodb_table.batch_state.arn]
  }

  # Handler failures are delivered to the shared events DLQ via the async
  # invoke on_failure destination below; Lambda sends with the function role.
  statement {
    sid       = "FailureDestination"
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.events_dlq.arn]
  }

  # Logs, scoped to this function's log group.
  statement {
    sid       = "Logs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "${local.prefix}-ingest-policy"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

# --- Lambda ----------------------------------------------------------------

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/${local.prefix}-ingest"
  retention_in_days = 7
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${local.prefix}-ingest"
  role             = aws_iam_role.ingest.arn
  runtime          = "python3.13"
  handler          = "handler.handler"
  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      PIPELINE_BUCKET   = aws_s3_bucket.pipeline.bucket
      BATCH_STATE_TABLE = aws_dynamodb_table.batch_state.name
    }
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.ingest.name
  }

  depends_on = [aws_iam_role_policy.ingest]
}

# EventBridge invokes Lambda targets asynchronously, so the rule's DLQ only
# covers delivery failures. Handler errors are retried by Lambda's async
# machinery and then surfaced to the shared events DLQ via on_failure.
resource "aws_lambda_function_event_invoke_config" "ingest" {
  function_name          = aws_lambda_function.ingest.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.events_dlq.arn
    }
  }
}

# --- EventBridge target: landing/ Object Created -> ingest Lambda -----------

resource "aws_lambda_permission" "ingest_from_landing_rule" {
  statement_id   = "AllowLandingObjectCreatedRule"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.ingest.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.landing_object_created.arn
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_cloudwatch_event_target" "landing_to_ingest" {
  rule = aws_cloudwatch_event_rule.landing_object_created.name
  arn  = aws_lambda_function.ingest.arn

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 3
  }

  dead_letter_config {
    arn = aws_sqs_queue.events_dlq.arn
  }
}

# EventBridge must be allowed to write undeliverable landing events to the
# shared DLQ; the condition pins the permission to this rule only.
resource "aws_sqs_queue_policy" "events_dlq_allow_landing_rule" {
  queue_url = aws_sqs_queue.events_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowLandingRuleDeadLetter"
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.events_dlq.arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = aws_cloudwatch_event_rule.landing_object_created.arn }
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

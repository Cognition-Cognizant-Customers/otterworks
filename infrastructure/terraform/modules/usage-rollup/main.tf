# ------------------------------------------------------------------------------
# OtterWorks Usage-Rollup Module (event-driven "after" state)
#
# Replaces the legacy nightly usage-rollup CronJob with an incremental,
# event-driven pipeline:
#
#   analytics event -> EventBridge rule -> SQS queue (with DLQ) -> Lambda
#                                                                  incremental
#                                                                  rollup upsert
#                                                                  into DynamoDB
#
# The Lambda reuses the analytics-service aggregation semantics
# (com.otterworks.analytics.event.UsageRollupLambdaHandler), so rollups match
# the deterministic batch output byte-for-byte while staying fresh within
# seconds instead of up to 24 h. See docs/BATCH-USAGE-ROLLUP.md.
# ------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name = "${var.project}-usage-rollup-${var.environment}"

  # The rule listens on the account's default event bus.
  event_bus_arn = "arn:aws:events:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:event-bus/default"

  common_tags = {
    Module  = "usage-rollup"
    Project = var.project
    Service = "analytics-service"
  }
}

# --- EventBridge rule: route analytics usage events to SQS ---

resource "aws_cloudwatch_event_rule" "usage_events" {
  name        = local.name
  description = "Routes OtterWorks analytics usage events to the usage-rollup queue"

  event_pattern = jsonencode({
    source        = ["otterworks.analytics"]
    "detail-type" = ["AnalyticsEvent"]
  })

  tags = local.common_tags
}

resource "aws_cloudwatch_event_target" "usage_events_to_sqs" {
  rule = aws_cloudwatch_event_rule.usage_events.name
  arn  = aws_sqs_queue.usage_rollup.arn

  dead_letter_config {
    arn = aws_sqs_queue.usage_rollup_dlq.arn
  }
}

# --- SQS queue (buffer/retry) + dead-letter queue ---

resource "aws_sqs_queue" "usage_rollup" {
  name = "${local.name}-events"
  # >= 6x the Lambda timeout so in-flight batches never reappear mid-run.
  visibility_timeout_seconds = 180
  message_retention_seconds  = 259200
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.usage_rollup_dlq.arn
    maxReceiveCount     = 3
  })

  tags = local.common_tags
}

resource "aws_sqs_queue" "usage_rollup_dlq" {
  name                      = "${local.name}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = local.common_tags
}

# EventBridge delivers target failures to the DLQ under its own service
# principal, so the DLQ needs its own SendMessage policy.
resource "aws_sqs_queue_policy" "usage_rollup_dlq" {
  queue_url = aws_sqs_queue.usage_rollup_dlq.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.usage_rollup_dlq.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.usage_events.arn } }
    }]
  })
}

resource "aws_sqs_queue_policy" "usage_rollup" {
  queue_url = aws_sqs_queue.usage_rollup.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.usage_rollup.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.usage_events.arn } }
    }]
  })
}

# --- DynamoDB: one rollup item per calendar date (the upsert target) ---

resource "aws_dynamodb_table" "usage_rollups" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name         = "${var.project}-usage-rollups-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "date"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "date"
    type = "S"
  }

  tags = local.common_tags
}

# Processed-event ledger: one item per eventId, written conditionally in the
# same transaction as the rollup delta so redelivered events are never counted
# twice. TTL-expired after the dedupe window.
resource "aws_dynamodb_table" "processed_events" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name         = "${var.project}-usage-rollup-processed-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "eventId"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "eventId"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  tags = local.common_tags
}

# --- Lambda: incremental rollup upsert ---

resource "aws_iam_role" "lambda" {
  name = "${local.name}-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "lambda" {
  name = "${local.name}-lambda"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.usage_rollup.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = [aws_dynamodb_table.usage_rollups.arn, aws_dynamodb_table.processed_events.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
        Resource = "*"
      }
    ]
  })
}

resource "aws_lambda_function" "usage_rollup" {
  function_name = local.name
  role          = aws_iam_role.lambda.arn

  # Fat jar built from analytics-service (sbt assembly); the handler class is
  # compiled alongside the legacy batch job so both share aggregation semantics.
  # `filebase64sha256` is evaluated at plan time, so it is guarded with
  # `fileexists` to keep `terraform plan`/`apply` of unrelated resources working
  # from a clean checkout where the jar has not been assembled yet. Creating or
  # updating this function still requires `sbt assembly` first.
  filename         = var.lambda_jar_path
  source_code_hash = fileexists(var.lambda_jar_path) ? filebase64sha256(var.lambda_jar_path) : null
  handler          = "com.otterworks.analytics.event.UsageRollupLambdaHandler::handleRequest"
  runtime          = "java17"
  memory_size      = 512
  timeout          = 30

  # Environment variables hold only the (non-sensitive) DynamoDB table name and
  # are encrypted at rest with the AWS-managed Lambda key.
  # nosemgrep: terraform.aws.security.aws-lambda-environment-unencrypted.aws-lambda-environment-unencrypted
  environment {
    variables = {
      ROLLUP_TABLE = aws_dynamodb_table.usage_rollups.name
      DEDUPE_TABLE = aws_dynamodb_table.processed_events.name
    }
  }

  tracing_config {
    mode = "Active"
  }

  tags = local.common_tags
}

resource "aws_lambda_event_source_mapping" "usage_rollup" {
  event_source_arn                   = aws_sqs_queue.usage_rollup.arn
  function_name                      = aws_lambda_function.usage_rollup.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  function_response_types            = ["ReportBatchItemFailures"]

  # AWS validates the execution role's SQS permissions when the mapping is
  # created, so the inline policy must exist first.
  depends_on = [aws_iam_role_policy.lambda]
}

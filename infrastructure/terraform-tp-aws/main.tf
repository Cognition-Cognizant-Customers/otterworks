# ------------------------------------------------------------------------------
# OtterWorks Tech-Partnerships — AWS serverless "after" state
#
# Replaces the legacy pet-box CUSTBILL chain (etl/legacy-extra/: SFTP poll ->
# fixed-width parse -> finance report on cron) with an event-driven pipeline:
#
#   S3 landing/<ns>/CUSTBILL_*.dat
#     -> EventBridge (S3 Object Created)
#       -> SQS (with DLQ)
#         -> Lambda trigger
#           -> Step Functions: Parse (S3 .psv + DynamoDB) -> FinanceReport (S3 CSV)
#
# Everything is serverless/on-demand (no hourly-cost resources) and fully
# removed by `terraform destroy`.
# ------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  bucket     = "${var.name_prefix}-ingest-${local.account_id}"
}

# --- S3 landing/parsed/reports bucket ---

resource "aws_s3_bucket" "ingest" {
  bucket        = local.bucket
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "ingest" {
  bucket                  = aws_s3_bucket.ingest.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "ingest" {
  bucket = aws_s3_bucket.ingest.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_policy" "ingest_tls_only" {
  bucket = aws_s3_bucket.ingest.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.ingest.arn,
          "${aws_s3_bucket.ingest.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.ingest]
}

resource "aws_s3_bucket_notification" "ingest" {
  bucket      = aws_s3_bucket.ingest.id
  eventbridge = true
}

# --- DynamoDB (on-demand) for parsed billing records ---

resource "aws_dynamodb_table" "billing" {
  name         = "${var.name_prefix}-billing-records"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ns"
  range_key    = "rec"

  attribute {
    name = "ns"
    type = "S"
  }

  attribute {
    name = "rec"
    type = "S"
  }
}

# --- SQS queue + DLQ fed by EventBridge ---

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${var.name_prefix}-ingest-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${var.name_prefix}-ingest-queue"
  visibility_timeout_seconds = 360

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.ingest.arn
        Condition = {
          ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.landing.arn }
        }
      }
    ]
  })
}

# --- EventBridge rule: landing/ object created -> SQS ---

resource "aws_cloudwatch_event_rule" "landing" {
  name        = "${var.name_prefix}-landing-object-created"
  description = "CUSTBILL file landed in the ingest bucket"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.ingest.bucket] }
      object = { key = [{ prefix = "landing/" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "landing_to_sqs" {
  rule = aws_cloudwatch_event_rule.landing.name
  arn  = aws_sqs_queue.ingest.arn
}

# --- Lambda packaging (shared zip, three handlers) ---

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../services/serverless-ingest/src"
  output_path = "${path.module}/.build/serverless-ingest.zip"
}

locals {
  lambdas = {
    trigger = "handler_trigger.handler"
    parse   = "handler_parse.handler"
    report  = "handler_report.handler"
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = local.lambdas
  name              = "/aws/lambda/${var.name_prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "fn" {
  for_each = local.lambdas

  function_name    = "${var.name_prefix}-${each.key}"
  role             = aws_iam_role.lambda[each.key].arn
  handler          = each.value
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 256
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = {
      TABLE_NAME        = aws_dynamodb_table.billing.name
      BUCKET            = aws_s3_bucket.ingest.bucket
      STATE_MACHINE_ARN = "arn:aws:states:${var.aws_region}:${local.account_id}:stateMachine:${var.name_prefix}-custbill-pipeline"
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_event_source_mapping" "sqs_to_trigger" {
  event_source_arn = aws_sqs_queue.ingest.arn
  function_name    = aws_lambda_function.fn["trigger"].arn
  batch_size       = 10
}

# --- Step Functions: Parse -> FinanceReport ---

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/aws/states/${var.name_prefix}-custbill-pipeline"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.name_prefix}-custbill-pipeline"
  role_arn = aws_iam_role.sfn.arn

  definition = jsonencode({
    Comment = "CUSTBILL serverless pipeline: parse fixed-width extract, then regenerate the finance report"
    StartAt = "ParseCustbill"
    States = {
      ParseCustbill = {
        Type       = "Task"
        Resource   = aws_lambda_function.fn["parse"].arn
        ResultPath = "$.parse"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 2
          MaxAttempts     = 2
          BackoffRate     = 2
        }]
        Next = "FinanceReport"
      }
      FinanceReport = {
        Type       = "Task"
        Resource   = aws_lambda_function.fn["report"].arn
        ResultPath = "$.report"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 2
          MaxAttempts     = 2
          BackoffRate     = 2
        }]
        End = true
      }
    }
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }
}

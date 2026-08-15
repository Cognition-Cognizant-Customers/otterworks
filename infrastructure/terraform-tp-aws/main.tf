# ------------------------------------------------------------------------------
# OtterWorks Tech-Partnerships — AWS serverless CUSTBILL pipeline (shared skeleton)
#
# Replaces the legacy pet-box CUSTBILL chain (etl/legacy-extra/: SFTP poll ->
# fixed-width parse -> finance report, sequenced by sleeps) with an event-driven
# pipeline:
#
#   s3://<bucket>/landing/<ns>/CUSTBILL_*.dat
#     -> EventBridge (S3 "Object Created")
#       -> SQS (+ DLQ)
#         -> trigger Lambda            (component: ingest)
#           -> Step Functions          (component: report/orchestration)
#                Parse   -> parsed/<ns>/*.psv + DynamoDB   (component: parse)
#                Report  -> reports/<ns>/finance_billing_<stamp>.csv|.xls
#
# This file owns only the SHARED substrate (bucket, event bus wiring, queue,
# table, packaging, naming). Each pipeline component lives in its own
# component-*.tf file and is referenced by deterministic name, so components can
# be developed and merged independently — see
# docs/tech-partnerships/contracts/aws-serverless-*.md.
#
# Everything is serverless/on-demand (no hourly-cost resources) and fully
# removed by `terraform destroy`.
# ------------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  bucket     = "${var.name_prefix}-ingest-${local.account_id}"

  # Deterministic cross-component references. Components must NOT reference each
  # other's Terraform resources directly (they land in separate PRs); they use
  # these constructed names/ARNs instead.
  state_machine_name = "${var.name_prefix}-custbill-pipeline"
  state_machine_arn  = "arn:aws:states:${var.aws_region}:${local.account_id}:stateMachine:${var.name_prefix}-custbill-pipeline"

  lambda_names = {
    trigger = "${var.name_prefix}-trigger"
    parse   = "${var.name_prefix}-parse"
    report  = "${var.name_prefix}-report"
  }

  lambda_arns = {
    for k, name in local.lambda_names :
    k => "arn:aws:lambda:${var.aws_region}:${local.account_id}:function:${name}"
  }

  # Environment every handler gets; keeps the S3/DynamoDB/state-machine contract
  # in one place.
  lambda_env = {
    BUCKET            = aws_s3_bucket.ingest.bucket
    TABLE_NAME        = aws_dynamodb_table.billing.name
    STATE_MACHINE_ARN = local.state_machine_arn
    TZ                = var.report_tz
  }
}

# --- S3: landing/, parsed/, reports/ prefixes in one bucket ---

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

# --- Shared Lambda packaging: one zip, one handler module per component ---

data "archive_file" "lambda" {
  type        = "zip"
  source_dir  = "${path.module}/../../services/serverless-ingest/src"
  output_path = "${path.module}/.build/serverless-ingest.zip"
  excludes    = ["__pycache__"]
}

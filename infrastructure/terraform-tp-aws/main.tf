# Shared skeleton for the AWS serverless track (parent-applied).
#
# Landing bucket + EventBridge + SQS (with DLQ) + DynamoDB batch-state table
# + base IAM. Components (ingest / parser / report) contribute their own
# <unit>.tf files to this stack; only the parent session runs plan/apply.

data "aws_caller_identity" "current" {}

# --- S3: single pipeline bucket, stage-per-prefix -------------------------
# landing/   mainframe drop (replaces the SFTP drop dir)
# incoming/  staged by ingest (replaces $ROOT/incoming)
# archive/   timestamp-free content-addressed archive (replaces $ROOT/archive)
# parsed/    parser output .psv (replaces $ROOT/parsed)
# reports/   finance report .csv/.xls (replaces $ROOT/reports)

resource "aws_s3_bucket" "pipeline" {
  bucket        = "${local.prefix}-pipeline-${data.aws_caller_identity.current.account_id}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "pipeline" {
  bucket                  = aws_s3_bucket.pipeline.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_notification" "pipeline" {
  bucket      = aws_s3_bucket.pipeline.id
  eventbridge = true
}

# --- SQS: parse work queue + dead-letter queue ----------------------------

resource "aws_sqs_queue" "events_dlq" {
  name                      = "${local.prefix}-events-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "events" {
  name                       = "${local.prefix}-events"
  visibility_timeout_seconds = 120
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.events_dlq.arn
    maxReceiveCount     = 3
  })
}

# --- EventBridge: object-arrival is the trigger, not a cron ---------------
# landing/ arrivals go to the ingest component (it attaches its own target);
# incoming/ arrivals are queued for the parser via SQS.

resource "aws_cloudwatch_event_rule" "landing_object_created" {
  name = "${local.prefix}-landing-object-created"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.pipeline.bucket] }
      object = { key = [{ prefix = "landing/" }] }
    }
  })
}

resource "aws_cloudwatch_event_rule" "incoming_object_created" {
  name = "${local.prefix}-incoming-object-created"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = { name = [aws_s3_bucket.pipeline.bucket] }
      object = { key = [{ prefix = "incoming/" }] }
    }
  })
}

resource "aws_cloudwatch_event_target" "incoming_to_events_queue" {
  rule = aws_cloudwatch_event_rule.incoming_object_created.name
  arn  = aws_sqs_queue.events.arn
}

resource "aws_sqs_queue_policy" "events_allow_eventbridge" {
  queue_url = aws_sqs_queue.events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.events.arn
      Condition = {
        ArnEquals    = { "aws:SourceArn" = aws_cloudwatch_event_rule.incoming_object_created.arn }
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })
}

# --- DynamoDB: batch state / anomaly ledger (on-demand only) --------------

resource "aws_dynamodb_table" "batch_state" {
  name         = "${local.prefix}-batch-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }
}

# --- Base IAM -----------------------------------------------------------
# Components define their own least-privilege roles in <unit>.tf; each
# Lambda trust policy must carry an aws:SourceAccount condition, and no
# component role may carry bucket-wide write.

# cron-archive unit: the configuration that replaces etl/scripts/audit_archive_weekly.py.
#
# DynamoDB TTL (on the shared table, already enabled) decides when an audit event
# expires; a stream-driven Lambda persists each expiring item to S3 before the
# expiry loses it; an S3 lifecycle rule moves the archive to Glacier. Nothing here
# is scheduled — there is no EventBridge rule and no cron expression, which is the
# point of the unit.

data "archive_file" "audit_archive" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/audit_archive"
  output_path = "${path.module}/build/audit_archive.zip"
}

resource "aws_iam_role" "audit_archive" {
  name = "${local.name_prefix}audit-archive-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

data "aws_iam_policy_document" "audit_archive" {
  statement {
    sid = "ReadAuditEvents"
    actions = [
      "dynamodb:Scan",
      "dynamodb:DescribeTable",
      "dynamodb:DescribeTimeToLive",
    ]
    resources = [aws_dynamodb_table.audit_events.arn]
  }

  statement {
    sid = "ReadAuditEventStream"
    actions = [
      "dynamodb:DescribeStream",
      "dynamodb:GetRecords",
      "dynamodb:GetShardIterator",
      "dynamodb:ListStreams",
    ]
    resources = ["${aws_dynamodb_table.audit_events.arn}/stream/*"]
  }

  statement {
    sid       = "WriteArchiveObjects"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.audit_archive.arn}/${var.audit_archive_prefix}/*"]
  }

  statement {
    sid       = "PublishArchiveMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
  }

  statement {
    sid       = "WriteLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.audit_archive.arn}:*"]
  }

  statement {
    sid       = "ReportFailures"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.audit_archive_dlq.arn]
  }
}

resource "aws_iam_role_policy" "audit_archive" {
  name   = "${local.name_prefix}audit-archive-policy"
  role   = aws_iam_role.audit_archive.id
  policy = data.aws_iam_policy_document.audit_archive.json
}

resource "aws_cloudwatch_log_group" "audit_archive" {
  name              = "/aws/lambda/${local.name_prefix}audit-archive"
  retention_in_days = 14
}

resource "aws_sqs_queue" "audit_archive_dlq" {
  name                      = "${local.name_prefix}audit-archive-dlq"
  message_retention_seconds = 1209600
}

resource "aws_lambda_function" "audit_archive" {
  function_name    = "${local.name_prefix}audit-archive"
  role             = aws_iam_role.audit_archive.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  filename         = data.archive_file.audit_archive.output_path
  source_code_hash = data.archive_file.audit_archive.output_base64sha256
  timeout          = 300
  memory_size      = 512

  environment {
    variables = {
      TABLE_NAME       = aws_dynamodb_table.audit_events.name
      ARCHIVE_BUCKET   = aws_s3_bucket.audit_archive.bucket
      ARCHIVE_PREFIX   = var.audit_archive_prefix
      TTL_ATTRIBUTE    = "expires_at"
      RETENTION_DAYS   = tostring(var.audit_retention_days)
      METRIC_NAMESPACE = "${local.name_prefix}audit-archive"
    }
  }

  depends_on = [aws_cloudwatch_log_group.audit_archive]
}

# The only trigger: TTL-driven REMOVE events. No schedule, no cron rule.
resource "aws_lambda_event_source_mapping" "audit_archive_ttl" {
  event_source_arn                   = aws_dynamodb_table.audit_events.stream_arn
  function_name                      = aws_lambda_function.audit_archive.arn
  starting_position                  = "LATEST"
  batch_size                         = 100
  maximum_batching_window_in_seconds = 30
  maximum_retry_attempts             = 3

  # TTL expiries only: DynamoDB attributes its own deletions to the service
  # principal, so operator/application deletes never reach the function.
  filter_criteria {
    filter {
      pattern = jsonencode({
        eventName    = ["REMOVE"]
        userIdentity = { type = ["Service"], principalId = ["dynamodb.amazonaws.com"] }
      })
    }
  }

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.audit_archive_dlq.arn
    }
  }

  # The mapping is rejected until the function may read the stream.
  depends_on = [aws_iam_role_policy.audit_archive]
}

resource "aws_s3_bucket_lifecycle_configuration" "audit_archive" {
  bucket = aws_s3_bucket.audit_archive.id

  rule {
    id     = "${local.name_prefix}audit-archive-glacier"
    status = "Enabled"

    filter {
      prefix = "${var.audit_archive_prefix}/"
    }

    # Same storage class the legacy job wrote directly, now expressed as
    # configuration. No expiration: archived audit events are never deleted.
    transition {
      days          = 0
      storage_class = "GLACIER"
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "audit_archive_errors" {
  alarm_name          = "${local.name_prefix}audit-archive-errors"
  alarm_description   = "Audit archival failed; expiring items may be lost before archival."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.audit_archive.function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "audit_archive_dlq" {
  alarm_name          = "${local.name_prefix}audit-archive-dlq"
  alarm_description   = "Stream batches failed past retry; expiring items may be unarchived."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.audit_archive_dlq.name
  }
}

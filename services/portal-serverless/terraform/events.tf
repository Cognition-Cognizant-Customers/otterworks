# Async event path for the feedback context: the feedback Lambda publishes
# FeedbackSubmitted to the custom bus after the DynamoDB write commits
# (write-then-publish; the synchronous 201 is untouched). A rule routes the
# detail-type to an SQS queue consumed by the projection Lambda, which
# maintains the derived feedback-stats read model idempotently. Failed
# messages redrive to a DLQ that an operator can replay with
# scripts/tp_portal/replay_dlq.py — the monolith could only lose them.

resource "aws_cloudwatch_event_bus" "portal" {
  name = "${local.prefix}-bus"
}

resource "aws_cloudwatch_event_rule" "feedback_submitted" {
  name           = "${local.prefix}-feedback-submitted"
  description    = "Route FeedbackSubmitted domain events to the projection queue and triage workflow."
  event_bus_name = aws_cloudwatch_event_bus.portal.name

  event_pattern = jsonencode({
    source      = ["otterworks.portal.feedback"]
    detail-type = ["FeedbackSubmitted"]
  })
}

# Visibility timeout must exceed the consumer's function timeout with real
# headroom (10s vs 5s here): if they were equal, a batch running to the wire
# would reappear on the queue before its delete, be double-processed, and
# after maxReceiveCount rounds park healthy events in the DLQ. Both stay
# small so a genuine poison message still exhausts maxReceiveCount and lands
# in the DLQ within a demo beat (~30s).
resource "aws_sqs_queue" "feedback_events" {
  name                       = "${local.prefix}-feedback-events"
  visibility_timeout_seconds = 10
  message_retention_seconds  = 86400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.feedback_events_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "feedback_events_dlq" {
  name                      = "${local.prefix}-feedback-events-dlq"
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue_redrive_allow_policy" "feedback_events_dlq" {
  queue_url = aws_sqs_queue.feedback_events_dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.feedback_events.arn]
  })
}

data "aws_iam_policy_document" "feedback_events_queue" {
  statement {
    sid     = "AllowEventBridgeRuleOnly"
    actions = ["sqs:SendMessage"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    resources = [aws_sqs_queue.feedback_events.arn]
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.feedback_submitted.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "feedback_events" {
  queue_url = aws_sqs_queue.feedback_events.id
  policy    = data.aws_iam_policy_document.feedback_events_queue.json
}

resource "aws_cloudwatch_event_target" "feedback_to_queue" {
  rule           = aws_cloudwatch_event_rule.feedback_submitted.name
  event_bus_name = aws_cloudwatch_event_bus.portal.name
  target_id      = "projection-queue"
  arn            = aws_sqs_queue.feedback_events.arn
}

# Producer side: the feedback Lambda may publish to this bus and nothing else.
data "aws_iam_policy_document" "feedback_publish" {
  statement {
    sid       = "PublishToPortalBusOnly"
    actions   = ["events:PutEvents"]
    resources = [aws_cloudwatch_event_bus.portal.arn]
  }
}

resource "aws_iam_role_policy" "feedback_publish" {
  name   = "${local.prefix}-feedback-publish"
  role   = aws_iam_role.service["feedback"].id
  policy = data.aws_iam_policy_document.feedback_publish.json
}

# Derived read model: stats item + evt#/triage# idempotency markers.
resource "aws_dynamodb_table" "feedback_stats" {
  name         = "${local.prefix}-feedback-stats"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_iam_role" "projection" {
  name               = "${local.prefix}-feedback-projection-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "projection" {
  statement {
    sid = "ConsumeOwnQueueOnly"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.feedback_events.arn]
  }

  statement {
    sid = "WriteOwnProjectionOnly"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
    ]
    resources = [aws_dynamodb_table.feedback_stats.arn]
  }

  statement {
    sid = "Logs"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["arn:aws:logs:*:*:*"]
  }

  statement {
    sid = "Xray"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "projection" {
  name   = "${local.prefix}-feedback-projection-policy"
  role   = aws_iam_role.projection.id
  policy = data.aws_iam_policy_document.projection.json
}

# No SnapStart here: an event-source-mapping-driven consumer has no cold-start
# latency story, and the pending-optimization window makes early invocations
# fail in a way that looks like a code fault.
resource "aws_lambda_function" "projection" {
  function_name    = "${local.prefix}-feedback-projection"
  role             = aws_iam_role.projection.arn
  runtime          = "java17"
  handler          = "com.otterworks.portal.projection.Handler::handleRequest"
  filename         = "${path.module}/../feedback-projection-service/target/feedback-projection-service.jar"
  source_code_hash = filebase64sha256("${path.module}/../feedback-projection-service/target/feedback-projection-service.jar")
  memory_size      = var.lambda_memory_mb
  timeout          = 5

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      STATS_TABLE_NAME = aws_dynamodb_table.feedback_stats.name
    }
  }
}

resource "aws_lambda_event_source_mapping" "projection" {
  event_source_arn        = aws_sqs_queue.feedback_events.arn
  function_name           = aws_lambda_function.projection.arn
  batch_size              = 5
  function_response_types = ["ReportBatchItemFailures"]

  depends_on = [aws_iam_role_policy.projection]
}

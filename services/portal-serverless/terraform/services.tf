resource "aws_dynamodb_table" "context" {
  for_each = local.services

  name         = "${local.prefix}-${each.key}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = each.value.hash_key

  point_in_time_recovery {
    enabled = true
  }

  attribute {
    name = each.value.hash_key
    type = each.value.key_type
  }

  dynamic "attribute" {
    for_each = each.value.needs_gsi ? [1] : []
    content {
      name = "userId"
      type = "S"
    }
  }

  dynamic "global_secondary_index" {
    for_each = each.value.needs_gsi ? [1] : []
    content {
      name            = "by-user"
      hash_key        = "userId"
      projection_type = "ALL"
    }
  }
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "service" {
  for_each = local.services

  name               = "${local.prefix}-${each.key}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "service" {
  for_each = local.services

  statement {
    sid = "OwnTableOnly"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [
      aws_dynamodb_table.context[each.key].arn,
      "${aws_dynamodb_table.context[each.key].arn}/index/*",
    ]
  }

  dynamic "statement" {
    for_each = each.key == "feedback" ? [1] : []
    content {
      sid       = "PublishFeedbackEvents"
      actions   = ["events:PutEvents"]
      resources = [aws_cloudwatch_event_bus.portal.arn]
    }
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

resource "aws_iam_role_policy" "service" {
  for_each = local.services

  name   = "${local.prefix}-${each.key}-policy"
  role   = aws_iam_role.service[each.key].id
  policy = data.aws_iam_policy_document.service[each.key].json
}

resource "aws_lambda_function" "service" {
  for_each = local.services

  function_name    = "${local.prefix}-${each.key}"
  role             = aws_iam_role.service[each.key].arn
  runtime          = "java17"
  handler          = each.value.handler
  filename         = each.value.jar
  source_code_hash = filebase64sha256(each.value.jar)
  memory_size      = var.lambda_memory_mb
  timeout          = 15
  publish          = true

  snap_start {
    apply_on = "PublishedVersions"
  }

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      TABLE_NAME     = aws_dynamodb_table.context[each.key].name
      EVENT_BUS_NAME = each.key == "feedback" ? aws_cloudwatch_event_bus.portal.name : ""
      NAMESPACE      = var.namespace
    }
  }
}

# SnapStart only applies to published versions, so traffic goes through an alias.
resource "aws_lambda_alias" "live" {
  for_each = local.services

  name             = "live"
  function_name    = aws_lambda_function.service[each.key].function_name
  function_version = aws_lambda_function.service[each.key].version
}

resource "aws_apigatewayv2_integration" "service" {
  for_each = local.services

  api_id                 = aws_apigatewayv2_api.portal.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_alias.live[each.key].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "service" {
  for_each = local.service_routes

  api_id    = aws_apigatewayv2_api.portal.id
  route_key = each.value.route_key
  target    = "integrations/${aws_apigatewayv2_integration.service[each.value.service].id}"
}

resource "aws_lambda_permission" "apigw" {
  for_each = local.services

  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.service[each.key].function_name
  qualifier     = aws_lambda_alias.live[each.key].name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.portal.execution_arn}/*/*"
}

resource "aws_cloudwatch_event_bus" "portal" {
  name = "${local.prefix}-events"
}

resource "aws_sqs_queue" "feedback_events_dlq" {
  name                      = "${local.prefix}-feedback-events-dlq"
  message_retention_seconds = 345600
}

resource "aws_sqs_queue" "feedback_events" {
  name                       = "${local.prefix}-feedback-events"
  visibility_timeout_seconds = 5
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.feedback_events_dlq.arn
    maxReceiveCount     = 2
  })
}

data "aws_iam_policy_document" "feedback_queue" {
  statement {
    sid       = "AllowEventBridge"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.feedback_events.arn]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [aws_cloudwatch_event_rule.feedback_events.arn]
    }
  }
}

resource "aws_sqs_queue_policy" "feedback_events" {
  queue_url = aws_sqs_queue.feedback_events.url
  policy    = data.aws_iam_policy_document.feedback_queue.json
}

resource "aws_cloudwatch_event_rule" "feedback_events" {
  name           = "${local.prefix}-feedback-events"
  event_bus_name = aws_cloudwatch_event_bus.portal.name
  event_pattern = jsonencode({
    source      = ["otterworks.portal"]
    detail-type = ["feedback.submitted"]
  })
}

resource "aws_cloudwatch_event_target" "feedback_events" {
  rule           = aws_cloudwatch_event_rule.feedback_events.name
  event_bus_name = aws_cloudwatch_event_bus.portal.name
  target_id      = "feedback-events-queue"
  arn            = aws_sqs_queue.feedback_events.arn
}

resource "aws_dynamodb_table" "moderation" {
  name         = "${local.prefix}-moderation"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "idempotencyKey"

  attribute {
    name = "idempotencyKey"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

data "aws_iam_policy_document" "moderation_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "moderation" {
  name               = "${local.prefix}-moderation-role"
  assume_role_policy = data.aws_iam_policy_document.moderation_assume.json
}

data "aws_iam_policy_document" "moderation" {
  statement {
    sid       = "ModerationTable"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.moderation.arn]
  }

  statement {
    sid = "FeedbackQueue"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.feedback_events.arn]
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
    sid       = "Xray"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "moderation" {
  name   = "${local.prefix}-moderation-policy"
  role   = aws_iam_role.moderation.id
  policy = data.aws_iam_policy_document.moderation.json
}

resource "aws_lambda_function" "moderation" {
  function_name    = "${local.prefix}-moderation"
  role             = aws_iam_role.moderation.arn
  runtime          = "java17"
  handler          = "com.otterworks.portal.moderation.Handler::handleRequest"
  filename         = "${path.module}/../feedback-moderation/target/feedback-moderation.jar"
  source_code_hash = filebase64sha256("${path.module}/../feedback-moderation/target/feedback-moderation.jar")
  memory_size      = var.lambda_memory_mb
  timeout          = 15
  publish          = true

  snap_start {
    apply_on = "PublishedVersions"
  }

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      MODERATION_TABLE_NAME = aws_dynamodb_table.moderation.name
    }
  }
}

resource "aws_lambda_alias" "moderation_live" {
  name             = "live"
  function_name    = aws_lambda_function.moderation.function_name
  function_version = aws_lambda_function.moderation.version
}

resource "aws_lambda_event_source_mapping" "moderation" {
  event_source_arn                   = aws_sqs_queue.feedback_events.arn
  function_name                      = aws_lambda_alias.moderation_live.arn
  batch_size                         = 5
  maximum_batching_window_in_seconds = 0
  function_response_types            = ["ReportBatchItemFailures"]
}

# Incident wiring: a Lambda error trips a CloudWatch alarm; the alarm state change
# is matched on the default EventBridge bus and (when a webhook is configured)
# forwarded to Devin via an API destination — the estate pages Devin, not a human.

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = local.services

  alarm_name          = "${local.prefix}-${each.key}-errors"
  alarm_description   = "Errors in the ${each.key} context of the decomposed legacy portal."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = aws_lambda_function.service[each.key].function_name
  }
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${local.prefix}-api-5xx"
  alarm_description   = "HTTP 5xx responses from the decomposed portal API."
  namespace           = "AWS/ApiGateway"
  metric_name         = "5xx"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiId = aws_apigatewayv2_api.portal.id
    Stage = aws_apigatewayv2_stage.default.name
  }
}

resource "aws_cloudwatch_event_rule" "alarm_to_devin" {
  name        = "${local.prefix}-alarm-to-devin"
  description = "Route portal Lambda alarm state changes to the Devin incident webhook."

  event_pattern = jsonencode({
    source      = ["aws.cloudwatch"]
    detail-type = ["CloudWatch Alarm State Change"]
    resources = concat(
      [for a in aws_cloudwatch_metric_alarm.lambda_errors : a.arn],
      [aws_cloudwatch_metric_alarm.api_5xx.arn],
    )
    detail = {
      state = { value = ["ALARM"] }
    }
  })
}

resource "aws_cloudwatch_event_connection" "devin" {
  count = var.devin_webhook_url == "" ? 0 : 1

  name               = "${local.prefix}-devin-webhook"
  authorization_type = "API_KEY"

  auth_parameters {
    api_key {
      key   = "Authorization"
      value = var.devin_webhook_auth_header
    }
  }
}

resource "aws_cloudwatch_event_api_destination" "devin" {
  count = var.devin_webhook_url == "" ? 0 : 1

  name                             = "${local.prefix}-devin-webhook"
  connection_arn                   = aws_cloudwatch_event_connection.devin[0].arn
  invocation_endpoint              = var.devin_webhook_url
  http_method                      = "POST"
  invocation_rate_limit_per_second = 1
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "events_to_devin" {
  count = var.devin_webhook_url == "" ? 0 : 1

  name               = "${local.prefix}-events-to-devin"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

resource "aws_iam_role_policy" "events_to_devin" {
  count = var.devin_webhook_url == "" ? 0 : 1

  name = "${local.prefix}-events-to-devin"
  role = aws_iam_role.events_to_devin[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["events:InvokeApiDestination"]
      Resource = aws_cloudwatch_event_api_destination.devin[0].arn
    }]
  })
}

resource "aws_cloudwatch_event_target" "devin" {
  count = var.devin_webhook_url == "" ? 0 : 1

  rule     = aws_cloudwatch_event_rule.alarm_to_devin.name
  arn      = aws_cloudwatch_event_api_destination.devin[0].arn
  role_arn = aws_iam_role.events_to_devin[0].arn
}

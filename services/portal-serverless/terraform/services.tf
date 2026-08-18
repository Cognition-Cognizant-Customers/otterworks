resource "aws_dynamodb_table" "context" {
  for_each = local.services

  name         = "${local.prefix}-${each.key}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = each.value.hash_key

  attribute {
    name = each.value.hash_key
    type = each.value.key_type
  }

  point_in_time_recovery {
    enabled = true
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
    ]
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
    variables = merge(
      { TABLE_NAME = aws_dynamodb_table.context[each.key].name },
      each.key == "feedback" ? { EVENT_BUS_NAME = aws_cloudwatch_event_bus.portal.name } : {},
    )
  }
}

# SnapStart only applies to published versions, so traffic goes through an alias.
# The alias is also the canary seam: scripts/tp_portal/canary_deploy.py publishes
# new versions and shifts weighted routing on it, so Terraform must not fight the
# deploy tooling over the alias pointer.
resource "aws_lambda_alias" "live" {
  for_each = local.services

  name             = "live"
  function_name    = aws_lambda_function.service[each.key].function_name
  function_version = aws_lambda_function.service[each.key].version

  lifecycle {
    ignore_changes = [function_version, routing_config]
  }
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

  # Every route is closed by the token authorizer except the unauthenticated
  # GET /health liveness probe (no data, same body as the monolith's probe).
  authorization_type = each.value.route_key == "GET /health" ? "NONE" : "CUSTOM"
  authorizer_id      = each.value.route_key == "GET /health" ? null : aws_apigatewayv2_authorizer.token.id
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

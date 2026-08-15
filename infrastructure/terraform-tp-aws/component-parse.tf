# ------------------------------------------------------------------------------
# Parse component — fixed-width CUSTBILL records -> PSV + DynamoDB
# ------------------------------------------------------------------------------

resource "aws_iam_role" "parse" {
  name               = "${local.lambda_names["parse"]}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "parse_basic_execution" {
  role       = aws_iam_role.parse.name
  policy_arn = local.lambda_basic_execution_policy_arn
}

data "aws_iam_policy_document" "parse" {
  statement {
    sid    = "ParseS3Objects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]

    resources = ["${aws_s3_bucket.ingest.arn}/*"]
  }

  statement {
    sid    = "ParseBillingRecords"
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:Query",
      "dynamodb:DeleteItem",
    ]

    resources = [aws_dynamodb_table.billing.arn]
  }
}

resource "aws_iam_role_policy" "parse" {
  name   = "${local.lambda_names["parse"]}-policy"
  role   = aws_iam_role.parse.id
  policy = data.aws_iam_policy_document.parse.json
}

resource "aws_cloudwatch_log_group" "parse" {
  name              = "/aws/lambda/${local.lambda_names["parse"]}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "parse" {
  function_name    = local.lambda_names["parse"]
  role             = aws_iam_role.parse.arn
  handler          = "handler_parse.handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 512
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = local.lambda_env
  }

  depends_on = [aws_cloudwatch_log_group.parse]
}

# Parser component: Lambda consuming incoming/ Object Created events from the
# shared events queue (event-source mapping), writing byte-identical .psv
# output to parsed/ and recording counts + anomalies in the batch-state table.
# Replaces etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh.

# hashicorp/archive is builtin-resolvable; the shared versions.tf owns the
# single required_providers block and must stay untouched.
data "archive_file" "parser" {
  type        = "zip"
  source_dir  = "${path.module}/../../services/serverless-ingest/parser"
  output_path = "${path.module}/.terraform/build/parser.zip"
  excludes    = ["tests", "__pycache__"]
}

# --- IAM: least-privilege role -------------------------------------------

data "aws_iam_policy_document" "parser_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "parser" {
  name               = "${local.prefix}-parser-role"
  assume_role_policy = data.aws_iam_policy_document.parser_assume.json
}

data "aws_iam_policy_document" "parser" {
  statement {
    sid       = "ReadIncomingOnly"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.pipeline.arn}/incoming/*"]
  }

  statement {
    sid       = "WriteParsedOnly"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.pipeline.arn}/parsed/*"]
  }

  statement {
    sid       = "BatchStateLedger"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.batch_state.arn]
  }

  statement {
    sid    = "ConsumeEventsQueue"
    effect = "Allow"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.events.arn]
  }

  statement {
    sid       = "FunctionLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.parser.arn}:*"]
  }
}

resource "aws_iam_role_policy" "parser" {
  name   = "${local.prefix}-parser-policy"
  role   = aws_iam_role.parser.id
  policy = data.aws_iam_policy_document.parser.json
}

# --- Lambda ---------------------------------------------------------------

resource "aws_cloudwatch_log_group" "parser" {
  name              = "/aws/lambda/${local.prefix}-parser"
  retention_in_days = 14
}

resource "aws_lambda_function" "parser" {
  function_name    = "${local.prefix}-parser"
  role             = aws_iam_role.parser.arn
  runtime          = "python3.12"
  handler          = "handler.lambda_handler"
  filename         = data.archive_file.parser.output_path
  source_code_hash = data.archive_file.parser.output_base64sha256
  timeout          = 60
  memory_size      = 256

  environment {
    variables = {
      NS                = var.ns
      BATCH_STATE_TABLE = aws_dynamodb_table.batch_state.name
    }
  }

  depends_on = [aws_cloudwatch_log_group.parser]
}

# Per-file granularity and clean poison semantics: one object per invocation,
# so a bad message redrives alone to the shared DLQ after 3 receives.
resource "aws_lambda_event_source_mapping" "parser_events" {
  event_source_arn = aws_sqs_queue.events.arn
  function_name    = aws_lambda_function.parser.arn
  batch_size       = 1

  # The mapping polls SQS with the function role's credentials; make sure the
  # inline policy grants exist before Lambda validates the mapping.
  depends_on = [aws_iam_role_policy.parser]
}

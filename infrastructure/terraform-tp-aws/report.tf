# Report component: finance billing report Lambda + the ow-tp-<ns>-chain
# Step Functions state machine (replaces etl/legacy-extra/jobs/finance_excel_report.pl
# and the run_all.sh sleep-600 orchestration).
#
# The state machine is started explicitly per batch with
# {"ns": "<ns>", "report_date": "YYYYMMDD"} (optionally
# "expected_parsed_count": <n>): it verifies the parsed/ inputs are visible
# (no dependency guessing by sleeping), fails visibly when fewer objects
# than expected are present, invokes the report Lambda, and propagates any
# error to the execution (no 2>/dev/null || true).

# The hashicorp/archive provider requirement belongs in the shared
# versions.tf, which components must not edit; terraform init resolves the
# implied requirement from this data source.
# tflint-ignore: terraform_required_providers
data "archive_file" "report_lambda" {
  type        = "zip"
  source_file = "${path.module}/../../services/serverless-ingest/report/handler.py"
  output_path = "${path.module}/.build/report-lambda.zip"
}

# --- Lambda role: read parsed/*, write reports/* only ----------------------

data "aws_iam_policy_document" "report_lambda_assume" {
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

resource "aws_iam_role" "report" {
  name               = "${local.prefix}-report-role"
  assume_role_policy = data.aws_iam_policy_document.report_lambda_assume.json
}

data "aws_iam_policy_document" "report_lambda_permissions" {
  statement {
    sid       = "ListParsedPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.pipeline.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["parsed/*", "parsed/"]
    }
  }

  statement {
    sid       = "ReadParsedObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.pipeline.arn}/parsed/*"]
  }

  statement {
    sid       = "WriteReportsOnly"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.pipeline.arn}/reports/*"]
  }

  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.report_lambda.arn}:*"]
  }
}

resource "aws_iam_role_policy" "report" {
  name   = "${local.prefix}-report-policy"
  role   = aws_iam_role.report.id
  policy = data.aws_iam_policy_document.report_lambda_permissions.json
}

resource "aws_cloudwatch_log_group" "report_lambda" {
  name              = "/aws/lambda/${local.prefix}-report"
  retention_in_days = 14
}

resource "aws_lambda_function" "report" {
  function_name    = "${local.prefix}-report"
  role             = aws_iam_role.report.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.report_lambda.output_path
  source_code_hash = data.archive_file.report_lambda.output_base64sha256
  timeout          = 120
  memory_size      = 256

  # One batch at a time: concurrent report writes for the same date would
  # race on the same S3 keys.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      NS              = var.ns
      PIPELINE_BUCKET = aws_s3_bucket.pipeline.bucket
      ACCOUNT_ID      = data.aws_caller_identity.current.account_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.report_lambda]
}

# --- State machine role: invoke the report Lambda only ---------------------

data "aws_iam_policy_document" "chain_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "chain" {
  name               = "${local.prefix}-chain-role"
  assume_role_policy = data.aws_iam_policy_document.chain_assume.json
}

data "aws_iam_policy_document" "chain_permissions" {
  statement {
    sid       = "InvokeReportLambdaOnly"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.report.arn]
  }

  statement {
    sid       = "VerifyParsedInputs"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.pipeline.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["parsed/*", "parsed/"]
    }
  }

  # CloudWatch Logs delivery for Step Functions requires these actions on *
  # (log delivery is account-scoped; AWS does not support resource scoping).
  statement {
    sid    = "StateMachineLogDelivery"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:CreateLogStream",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutLogEvents",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "chain" {
  name   = "${local.prefix}-chain-policy"
  role   = aws_iam_role.chain.id
  policy = data.aws_iam_policy_document.chain_permissions.json
}

resource "aws_cloudwatch_log_group" "chain" {
  name              = "/aws/vendedlogs/states/${local.prefix}-chain"
  retention_in_days = 14
}

resource "aws_sfn_state_machine" "chain" {
  name     = "${local.prefix}-chain"
  role_arn = aws_iam_role.chain.arn

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.chain.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "Per-batch CUSTBILL chain: verify parsed inputs, run the finance report. Replaces run_all.sh sleep-600 orchestration; errors fail the execution visibly."
    StartAt = "VerifyParsedInputs"
    States = {
      VerifyParsedInputs = {
        Type     = "Task"
        Resource = "arn:aws:states:::aws-sdk:s3:listObjectsV2"
        Parameters = {
          Bucket = aws_s3_bucket.pipeline.bucket
          Prefix = "parsed/"
        }
        ResultSelector = {
          "parsedObjectCount.$" = "$.KeyCount"
        }
        ResultPath = "$.verify"
        Next       = "ParsedInputsSatisfyExpectation"
      }
      # Gate on the observed count when the caller states how many parsed
      # objects the batch expects; a shortfall fails the execution visibly
      # instead of reporting on partial data. Without expected_parsed_count
      # any count (including zero) is valid: the report Lambda writes a
      # header-only report and the execution succeeds, matching the legacy
      # exit-0 behaviour.
      #
      # Best-effort gate: KeyCount reflects a single unpaginated listing
      # (max 1000 keys) over the whole parsed/ prefix, including objects
      # the report Lambda ignores; a batch-accurate gate needs
      # batch-scoped parser output, which this layout does not have.
      ParsedInputsSatisfyExpectation = {
        Type = "Choice"
        Choices = [
          {
            Variable  = "$.expected_parsed_count"
            IsPresent = false
            Next      = "RunFinanceReport"
          },
          {
            Variable                     = "$.verify.parsedObjectCount"
            NumericGreaterThanEqualsPath = "$.expected_parsed_count"
            Next                         = "RunFinanceReport"
          },
        ]
        Default = "ParsedInputsMissing"
      }
      ParsedInputsMissing = {
        Type  = "Fail"
        Error = "ParsedInputsMissing"
        Cause = "parsed/ object count is below expected_parsed_count; refusing to report on partial data"
      }
      RunFinanceReport = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = {
          FunctionName = aws_lambda_function.report.arn
          Payload = {
            "ns.$"          = "$.ns"
            "report_date.$" = "$.report_date"
          }
        }
        ResultSelector = {
          "report.$" = "$.Payload"
        }
        ResultPath = "$.result"
        Retry = [{
          ErrorEquals = [
            "Lambda.ServiceException",
            "Lambda.AWSLambdaException",
            "Lambda.SdkClientException",
            "Lambda.TooManyRequestsException",
          ]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        End = true
      }
    }
  })
}

output "chain_state_machine_arn" {
  value = aws_sfn_state_machine.chain.arn
}

output "report_lambda_arn" {
  value = aws_lambda_function.report.arn
}

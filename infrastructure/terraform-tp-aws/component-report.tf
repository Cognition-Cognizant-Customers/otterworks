# ------------------------------------------------------------------------------
# Report component: finance report Lambda replacing finance_excel_report.pl and
# Step Functions orchestration replacing run_all.sh's sleep-based sequencing.
# References other components only through deterministic local.lambda_arns values.
# ------------------------------------------------------------------------------

resource "aws_iam_role" "report_lambda" {
  name               = "${local.lambda_names["report"]}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "report_lambda_basic" {
  role       = aws_iam_role.report_lambda.name
  policy_arn = local.lambda_basic_execution_policy_arn
}

resource "aws_iam_role_policy" "report_lambda_s3" {
  name = "${local.lambda_names["report"]}-s3"
  role = aws_iam_role.report_lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.ingest.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.ingest.arn}/*"
      },
    ]
  })
}

resource "aws_lambda_function" "report" {
  function_name    = local.lambda_names["report"]
  role             = aws_iam_role.report_lambda.arn
  handler          = "handler_report.handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 512
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = local.lambda_env
  }
}

resource "aws_cloudwatch_log_group" "report_lambda" {
  name              = "/aws/lambda/${local.lambda_names["report"]}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "pipeline" {
  name               = "${local.state_machine_name}-role"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
}

resource "aws_iam_role_policy" "pipeline" {
  name = "${local.state_machine_name}-invoke"
  role = aws_iam_role.pipeline.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = "lambda:InvokeFunction"
        Resource = [
          local.lambda_arns["parse"],
          local.lambda_arns["report"],
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/aws/states/${local.state_machine_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "pipeline" {
  name       = local.state_machine_name
  role_arn   = aws_iam_role.pipeline.arn
  depends_on = [aws_iam_role_policy.pipeline]

  definition = jsonencode({
    Comment = "Parse CUSTBILL files and generate the finance report"
    StartAt = "ParseCustbill"
    States = {
      ParseCustbill = {
        Type       = "Task"
        Resource   = local.lambda_arns["parse"]
        ResultPath = "$.parse"
        Retry = [
          {
            ErrorEquals = [
              "States.TaskFailed",
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException",
            ]
            MaxAttempts     = 2
            BackoffRate     = 2
            IntervalSeconds = 2
          },
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "PipelineFailed"
          },
        ]
        Next = "FinanceReport"
      }
      FinanceReport = {
        Type       = "Task"
        Resource   = local.lambda_arns["report"]
        ResultPath = "$.report"
        Retry = [
          {
            ErrorEquals = [
              "States.TaskFailed",
              "Lambda.ServiceException",
              "Lambda.AWSLambdaException",
              "Lambda.SdkClientException",
              "Lambda.TooManyRequestsException",
            ]
            MaxAttempts     = 2
            BackoffRate     = 2
            IntervalSeconds = 2
          },
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            ResultPath  = "$.error"
            Next        = "PipelineFailed"
          },
        ]
        End = true
      }
      PipelineFailed = {
        Type  = "Fail"
        Error = "PipelineFailed"
        Cause = "CUSTBILL pipeline stage failed"
      }
    }
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.pipeline.arn}:*"
    level                  = "ERROR"
    include_execution_data = true
  }
}

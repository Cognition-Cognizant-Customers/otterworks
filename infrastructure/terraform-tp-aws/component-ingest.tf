# ------------------------------------------------------------------------------
# Component: ingest — replaces etl/legacy-extra/jobs/sftp_ingest_poll.ksh
#
# The legacy job polled the SFTP drop three times, "settled" each file by
# comparing byte counts a second apart, and left a lock file behind forever.
# Here the arrival of the object IS the signal: EventBridge -> SQS -> this
# trigger Lambda -> one Step Functions execution per file.
#
# Cross-component references use the deterministic local.* names from main.tf,
# never another component's resources.
# ------------------------------------------------------------------------------

resource "aws_iam_role" "trigger" {
  name               = "${local.lambda_names["trigger"]}-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "trigger_basic" {
  role       = aws_iam_role.trigger.name
  policy_arn = local.lambda_basic_execution_policy_arn
}

data "aws_iam_policy_document" "trigger" {
  statement {
    sid       = "StartPipelineExecution"
    actions   = ["states:StartExecution"]
    resources = [local.state_machine_arn]
  }

  statement {
    sid = "ConsumeIngestQueue"
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.ingest.arn]
  }
}

resource "aws_iam_role_policy" "trigger" {
  name   = "${local.lambda_names["trigger"]}-inline"
  role   = aws_iam_role.trigger.id
  policy = data.aws_iam_policy_document.trigger.json
}

resource "aws_cloudwatch_log_group" "trigger" {
  name              = "/aws/lambda/${local.lambda_names["trigger"]}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "trigger" {
  function_name    = local.lambda_names["trigger"]
  role             = aws_iam_role.trigger.arn
  handler          = "handler_trigger.handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 256
  filename         = data.archive_file.lambda.output_path
  source_code_hash = data.archive_file.lambda.output_base64sha256

  environment {
    variables = local.lambda_env
  }

  depends_on = [
    aws_iam_role_policy_attachment.trigger_basic,
    aws_cloudwatch_log_group.trigger,
  ]
}

# SQS -> Lambda. ReportBatchItemFailures keeps a poison record from replaying the
# whole batch; failures alone retry and eventually land in the DLQ.
resource "aws_lambda_event_source_mapping" "trigger_from_sqs" {
  event_source_arn                   = aws_sqs_queue.ingest.arn
  function_name                      = aws_lambda_function.trigger.arn
  batch_size                         = 10
  function_response_types            = ["ReportBatchItemFailures"]
  maximum_batching_window_in_seconds = 0

  depends_on = [aws_iam_role_policy.trigger]
}

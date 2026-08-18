# Orchestrated feedback triage: validate -> record triage marker -> done, with
# explicit Retry/Catch replacing the monolith's in-process try/catch. Standard
# workflow (not Express) so every execution's history — including retries and
# the quarantine path — is browsable in the console during the demo.

resource "aws_sfn_state_machine" "feedback_triage" {
  name     = "${local.prefix}-feedback-triage"
  role_arn = aws_iam_role.triage.arn
  type     = "STANDARD"

  definition = jsonencode({
    Comment = "Feedback triage for the decomposed portal: one execution per FeedbackSubmitted event."
    StartAt = "Validate"
    States = {
      Validate = {
        Type = "Choice"
        Choices = [{
          And = [
            { Variable = "$.detail.eventId", IsPresent = true },
            { Variable = "$.detail.eventId", IsString = true },
            { Variable = "$.detail.rating", IsPresent = true },
            { Variable = "$.detail.rating", NumericGreaterThanEquals = 1 },
            { Variable = "$.detail.rating", NumericLessThanEquals = 5 },
          ]
          Next = "RecordTriage"
        }]
        Default = "Quarantine"
      }
      RecordTriage = {
        Type     = "Task"
        Resource = "arn:aws:states:::dynamodb:updateItem"
        Parameters = {
          TableName = aws_dynamodb_table.feedback_stats.name
          Key = {
            pk = { "S.$" = "States.Format('triage#{}', $.detail.eventId)" }
          }
          UpdateExpression = "SET triagedAt = :t, rating = :r ADD seenCount :one"
          ExpressionAttributeValues = {
            ":t"   = { "S.$" = "$$.State.EnteredTime" }
            ":r"   = { "N.$" = "States.Format('{}', $.detail.rating)" }
            ":one" = { N = "1" }
          }
        }
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 2
          MaxAttempts     = 3
          BackoffRate     = 2.0
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.triageError"
          Next        = "Quarantine"
        }]
        End = true
      }
      Quarantine = {
        Type     = "Task"
        Resource = "arn:aws:states:::sqs:sendMessage"
        Parameters = {
          QueueUrl        = aws_sqs_queue.feedback_events_dlq.url
          "MessageBody.$" = "States.JsonToString($)"
        }
        Next = "TriageFailed"
      }
      TriageFailed = {
        Type  = "Fail"
        Error = "FeedbackTriageFailed"
        Cause = "Validation or persistence failed after retries; event quarantined to the DLQ."
      }
    }
  })
}

resource "aws_iam_role" "triage" {
  name = "${local.prefix}-feedback-triage-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "states.amazonaws.com" }
    }]
  })
}

data "aws_iam_policy_document" "triage" {
  statement {
    sid       = "TriageMarkerOnly"
    actions   = ["dynamodb:UpdateItem"]
    resources = [aws_dynamodb_table.feedback_stats.arn]
  }

  statement {
    sid       = "QuarantineToDlqOnly"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.feedback_events_dlq.arn]
  }
}

resource "aws_iam_role_policy" "triage" {
  name   = "${local.prefix}-feedback-triage-policy"
  role   = aws_iam_role.triage.id
  policy = data.aws_iam_policy_document.triage.json
}

# Same rule as the projection queue: every FeedbackSubmitted event also starts
# one triage execution.
resource "aws_iam_role" "events_to_triage" {
  name = "${local.prefix}-events-to-triage"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "events.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "events_to_triage" {
  name = "${local.prefix}-events-to-triage"
  role = aws_iam_role.events_to_triage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = aws_sfn_state_machine.feedback_triage.arn
    }]
  })
}

resource "aws_cloudwatch_event_target" "feedback_to_triage" {
  rule           = aws_cloudwatch_event_rule.feedback_submitted.name
  event_bus_name = aws_cloudwatch_event_bus.portal.name
  target_id      = "triage-workflow"
  arn            = aws_sfn_state_machine.feedback_triage.arn
  role_arn       = aws_iam_role.events_to_triage.arn
}

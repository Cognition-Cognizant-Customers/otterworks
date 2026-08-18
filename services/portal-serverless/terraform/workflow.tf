# Orchestrated feedback triage: validate -> record triage marker -> done, with
# explicit Retry/Catch replacing the monolith's in-process try/catch. Standard
# workflow (not Express) so every execution's history — including retries and
# the quarantine path — is browsable in the console during the demo.

# Quarantine queue dedicated to the workflow: the consumer's DLQ carries
# redrive-captured poison only, so one bad event never lands on it twice and
# replay_dlq.py never re-injects a triage-generated payload.
resource "aws_sqs_queue" "feedback_triage_quarantine" {
  name                      = "${local.prefix}-feedback-triage-quarantine"
  message_retention_seconds = 1209600
}

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
            { Variable = "$.detail.rating", IsNumeric = true },
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
          QueueUrl        = aws_sqs_queue.feedback_triage_quarantine.url
          "MessageBody.$" = "States.JsonToString($)"
        }
        Next = "TriageFailed"
      }
      TriageFailed = {
        Type  = "Fail"
        Error = "FeedbackTriageFailed"
        Cause = "Validation or persistence failed after retries; event quarantined to the triage quarantine queue."
      }
    }
  })
}

data "aws_caller_identity" "current" {}

# Trust is confined to state machines in this account (aws:SourceArn would be
# circular here: the state machine references this role).
resource "aws_iam_role" "triage" {
  name = "${local.prefix}-feedback-triage-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "states.amazonaws.com" }
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
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
    sid       = "QuarantineQueueOnly"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.feedback_triage_quarantine.arn]
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
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.feedback_submitted.arn
        }
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
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

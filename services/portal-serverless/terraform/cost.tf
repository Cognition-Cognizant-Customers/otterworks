# Cost guardrail: the platform tells you before the bill does. A monthly AWS
# Budget scoped to the estate tag notifies an SNS topic at 80% actual and 100%
# forecasted spend. The monolith's VM had no equivalent — its cost was flat,
# always-on, and silent.
#
# Caveats (declared, not hidden):
#   - tag-scoped budgets only see spend once Project is activated as a cost
#     allocation tag in the payer account (Billing -> Cost allocation tags);
#   - budgets:* may be denied to the demo IAM user — set
#     enable_budget_guardrail=false and record the coverage gap in the recon.

resource "aws_sns_topic" "budget_alerts" {
  count = var.enable_budget_guardrail ? 1 : 0

  name = "${local.prefix}-budget-alerts"
}

resource "aws_sns_topic_policy" "budget_alerts" {
  count = var.enable_budget_guardrail ? 1 : 0

  arn = aws_sns_topic.budget_alerts[0].arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowBudgetsPublish"
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "SNS:Publish"
      Resource  = aws_sns_topic.budget_alerts[0].arn
      Condition = {
        StringEquals = {
          "aws:SourceAccount" = data.aws_caller_identity.current.account_id
        }
      }
    }]
  })
}

resource "aws_budgets_budget" "estate" {
  count = var.enable_budget_guardrail ? 1 : 0

  # Budgets validates SNS publish permission when the notification subscriber
  # is created, so the topic policy must exist first.
  depends_on = [aws_sns_topic_policy.budget_alerts]

  name         = "${local.prefix}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$otterworks-tp"]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts[0].arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts[0].arn]
  }
}

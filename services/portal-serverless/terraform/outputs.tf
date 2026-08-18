output "api_base_url" {
  description = "Public HTTPS base URL of the decomposed portal (paste into the Otter Portal demo page)."
  value       = aws_apigatewayv2_api.portal.api_endpoint
}

output "demo_site_url" {
  description = "S3-hosted Otter Portal demo page (empty when enable_demo_site=false)."
  value       = var.enable_demo_site ? "http://${aws_s3_bucket_website_configuration.demo_site[0].website_endpoint}" : ""
}

output "lambda_functions" {
  description = "Function name per bounded context."
  value       = { for name, fn in aws_lambda_function.service : name => fn.function_name }
}

output "dynamodb_tables" {
  description = "Table name per bounded context."
  value       = { for name, t in aws_dynamodb_table.context : name => t.name }
}

output "event_bus_name" {
  description = "Custom EventBridge bus carrying FeedbackSubmitted domain events."
  value       = aws_cloudwatch_event_bus.portal.name
}

output "feedback_events_queue_url" {
  description = "SQS queue feeding the feedback projection consumer."
  value       = aws_sqs_queue.feedback_events.url
}

output "feedback_events_dlq_url" {
  description = "DLQ holding poison feedback events (replay with scripts/tp_portal/replay_dlq.py)."
  value       = aws_sqs_queue.feedback_events_dlq.url
}

output "feedback_stats_table" {
  description = "Derived feedback-stats projection table."
  value       = aws_dynamodb_table.feedback_stats.name
}

output "feedback_triage_quarantine_url" {
  description = "Quarantine queue receiving events the triage workflow rejects (kept separate from the consumer DLQ)."
  value       = aws_sqs_queue.feedback_triage_quarantine.url
}

output "feedback_triage_state_machine_arn" {
  description = "Standard Step Functions workflow triaging each FeedbackSubmitted event."
  value       = aws_sfn_state_machine.feedback_triage.arn
}

output "demo_api_token" {
  description = "Bearer token for the closed front door (paste into the demo page's token field; transcript/load tooling reads PORTAL_API_TOKEN)."
  value       = random_password.demo_api_token.result
  sensitive   = true
}

output "authorizer_function" {
  description = "Lambda authorizer guarding every non-health route."
  value       = aws_lambda_function.authorizer.function_name
}

output "budget_alerts_topic_arn" {
  description = "SNS topic receiving budget guardrail notifications (empty when enable_budget_guardrail=false)."
  value       = var.enable_budget_guardrail ? aws_sns_topic.budget_alerts[0].arn : ""
}

output "demo_site_cdn_url" {
  description = "CloudFront (WAF-protected) URL of the demo page (empty when enable_demo_site=false)."
  value       = var.enable_demo_site ? "https://${aws_cloudfront_distribution.demo_site[0].domain_name}" : ""
}

output "demo_site_waf_arn" {
  description = "WAFv2 web ACL attached to the demo page CDN (empty when enable_demo_site=false)."
  value       = var.enable_demo_site ? aws_wafv2_web_acl.demo_site[0].arn : ""
}

output "error_alarms" {
  description = "CloudWatch alarm name per bounded context."
  value       = { for name, a in aws_cloudwatch_metric_alarm.lambda_errors : name => a.alarm_name }
}

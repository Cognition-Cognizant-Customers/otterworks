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

output "error_alarms" {
  description = "CloudWatch alarm name per bounded context."
  value       = { for name, a in aws_cloudwatch_metric_alarm.lambda_errors : name => a.alarm_name }
}

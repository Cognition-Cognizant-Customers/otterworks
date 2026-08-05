output "event_rule_arn" {
  description = "EventBridge rule routing analytics usage events to SQS"
  value       = aws_cloudwatch_event_rule.usage_events.arn
}

output "queue_url" {
  description = "SQS queue URL buffering usage events for the rollup Lambda"
  value       = aws_sqs_queue.usage_rollup.url
}

output "queue_arn" {
  description = "SQS queue ARN buffering usage events for the rollup Lambda"
  value       = aws_sqs_queue.usage_rollup.arn
}

output "dlq_arn" {
  description = "Dead-letter queue ARN for failed usage-rollup deliveries"
  value       = aws_sqs_queue.usage_rollup_dlq.arn
}

output "lambda_function_name" {
  description = "Lambda performing the incremental rollup upsert"
  value       = aws_lambda_function.usage_rollup.function_name
}

output "rollup_table_name" {
  description = "DynamoDB table holding one usage rollup per calendar date"
  value       = aws_dynamodb_table.usage_rollups.name
}

output "dedupe_table_name" {
  description = "DynamoDB processed-event ledger backing idempotent upserts"
  value       = aws_dynamodb_table.processed_events.name
}

output "event_bus_arn" {
  description = "EventBridge bus the usage-events rule listens on (default bus)"
  value       = local.event_bus_arn
}

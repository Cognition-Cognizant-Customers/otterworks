output "pipeline_bucket" {
  value = aws_s3_bucket.pipeline.bucket
}

output "events_queue_url" {
  value = aws_sqs_queue.events.url
}

output "events_queue_arn" {
  value = aws_sqs_queue.events.arn
}

output "events_dlq_url" {
  value = aws_sqs_queue.events_dlq.url
}

output "batch_state_table" {
  value = aws_dynamodb_table.batch_state.name
}

output "landing_rule_arn" {
  value = aws_cloudwatch_event_rule.landing_object_created.arn
}

output "prefix" {
  value = local.prefix
}

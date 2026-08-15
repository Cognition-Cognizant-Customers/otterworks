output "aws_region" {
  description = "Region the stack is applied in"
  value       = var.aws_region
}

output "report_tz" {
  description = "Timezone the report Lambda stamps report filenames with"
  value       = var.report_tz
}

output "ingest_bucket" {
  description = "S3 bucket with landing/, parsed/, reports/ prefixes"
  value       = aws_s3_bucket.ingest.bucket
}

output "billing_table" {
  description = "DynamoDB table holding parsed billing records"
  value       = aws_dynamodb_table.billing.name
}

output "ingest_queue_url" {
  description = "SQS queue EventBridge delivers landing-object events to"
  value       = aws_sqs_queue.ingest.url
}

output "ingest_dlq_url" {
  description = "Dead-letter queue for events the trigger Lambda could not process"
  value       = aws_sqs_queue.ingest_dlq.url
}

output "state_machine_arn" {
  description = "Step Functions CUSTBILL pipeline (created by the orchestration component)"
  value       = local.state_machine_arn
}

output "lambda_names" {
  description = "Deterministic Lambda names per component"
  value       = local.lambda_names
}

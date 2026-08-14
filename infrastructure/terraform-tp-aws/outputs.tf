output "aws_region" {
  description = "Region the stack is applied in"
  value       = var.aws_region
}

output "ingest_bucket" {
  description = "S3 bucket with landing/, parsed/, reports/ prefixes"
  value       = aws_s3_bucket.ingest.bucket
}

output "billing_table" {
  description = "DynamoDB table holding parsed billing records"
  value       = aws_dynamodb_table.billing.name
}

output "state_machine_arn" {
  description = "Step Functions CUSTBILL pipeline"
  value       = aws_sfn_state_machine.pipeline.arn
}

output "ingest_queue_url" {
  value = aws_sqs_queue.ingest.url
}

output "ingest_dlq_url" {
  value = aws_sqs_queue.ingest_dlq.url
}

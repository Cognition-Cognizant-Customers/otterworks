output "file_storage_bucket" {
  value = aws_s3_bucket.file_storage.bucket
}

output "file_quarantine_bucket" {
  value = aws_s3_bucket.file_quarantine.bucket
}

output "audit_archive_bucket" {
  value = aws_s3_bucket.audit_archive.bucket
}

output "audit_events_table" {
  value = aws_dynamodb_table.audit_events.name
}

output "file_metadata_table" {
  value = aws_dynamodb_table.file_metadata.name
}

output "orphan_audit_table" {
  value = aws_dynamodb_table.orphan_audit.name
}

output "orphan_quarantine_function_name" {
  value = aws_lambda_function.orphan_quarantine.function_name
}

output "orphan_detect_rule_name" {
  value = aws_cloudwatch_event_rule.orphan_detect.name
}

output "orphan_quarantine_dlq_url" {
  value = aws_sqs_queue.orphan_quarantine_dlq.url
}

output "orphan_quarantine_dlq_arn" {
  value = aws_sqs_queue.orphan_quarantine_dlq.arn
}

output "quarantine_lifecycle_bucket" {
  value = aws_s3_bucket.file_quarantine.bucket
}
output "audit_archive_function" {
  value = aws_lambda_function.audit_archive.function_name
}

output "audit_archive_prefix" {
  value = var.audit_archive_prefix
}

output "audit_archive_dlq_url" {
  value = aws_sqs_queue.audit_archive_dlq.url
}

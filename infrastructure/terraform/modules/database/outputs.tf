output "rds_endpoint" {
  description = "RDS PostgreSQL connection endpoint"
  value       = aws_db_instance.postgres.endpoint
}

output "rds_arn" {
  description = "ARN of the RDS PostgreSQL instance"
  value       = aws_db_instance.postgres.arn
}

output "file_metadata_table_name" {
  description = "DynamoDB table name for file metadata"
  value       = aws_dynamodb_table.file_metadata.name
}

output "file_metadata_table_arn" {
  description = "DynamoDB table ARN for file metadata"
  value       = aws_dynamodb_table.file_metadata.arn
}

output "audit_events_table_name" {
  description = "DynamoDB table name for audit events"
  value       = aws_dynamodb_table.audit_events.name
}

output "audit_events_table_arn" {
  description = "DynamoDB table ARN for audit events"
  value       = aws_dynamodb_table.audit_events.arn
}

output "notifications_table_name" {
  description = "DynamoDB table name for notifications"
  value       = aws_dynamodb_table.notifications.name
}

output "notifications_table_arn" {
  description = "DynamoDB table ARN for notifications"
  value       = aws_dynamodb_table.notifications.arn
}

output "folders_table_name" {
  description = "DynamoDB table name for folders"
  value       = aws_dynamodb_table.folders.name
}

output "folders_table_arn" {
  description = "DynamoDB table ARN for folders"
  value       = aws_dynamodb_table.folders.arn
}

output "file_versions_table_name" {
  description = "DynamoDB table name for file versions"
  value       = aws_dynamodb_table.file_versions.name
}

output "file_versions_table_arn" {
  description = "DynamoDB table ARN for file versions"
  value       = aws_dynamodb_table.file_versions.arn
}

output "file_shares_table_name" {
  description = "DynamoDB table name for file shares"
  value       = aws_dynamodb_table.file_shares.name
}

output "file_shares_table_arn" {
  description = "DynamoDB table ARN for file shares"
  value       = aws_dynamodb_table.file_shares.arn
}

output "analytics_events_table_name" {
  description = "DynamoDB table name for analytics events"
  value       = aws_dynamodb_table.analytics_events.name
}

output "analytics_events_table_arn" {
  description = "DynamoDB table ARN for analytics events"
  value       = aws_dynamodb_table.analytics_events.arn
}

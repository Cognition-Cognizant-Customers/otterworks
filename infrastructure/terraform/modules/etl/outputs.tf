output "state_machine_arns" {
  description = "ARNs of the ETL Step Functions state machines, keyed by pipeline"
  value       = { for name, sm in aws_sfn_state_machine.pipeline : name => sm.arn }
}

output "lambda_function_names" {
  description = "Names of the ETL Lambda functions, keyed by pipeline"
  value       = { for name, fn in aws_lambda_function.pipeline : name => fn.function_name }
}

output "alerts_topic_arn" {
  description = "SNS topic ARN for ETL failure alerts"
  value       = aws_sns_topic.etl_alerts.arn
}

output "quarantine_bucket_name" {
  description = "S3 bucket for quarantined orphan files"
  value       = aws_s3_bucket.quarantine.id
}

output "db_secret_arn" {
  description = "Secrets Manager secret holding ETL database credentials"
  value       = aws_secretsmanager_secret.etl_db.arn
}

output "meilisearch_secret_arn" {
  description = "Secrets Manager secret holding the MeiliSearch API key"
  value       = aws_secretsmanager_secret.meilisearch.arn
}

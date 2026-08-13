output "instance_id" {
  description = "EC2 instance ID of the rehosted legacy-portal VM"
  value       = aws_instance.app.id
}

output "instance_public_dns" {
  description = "Public DNS of the legacy-portal instance"
  value       = aws_instance.app.public_dns
}

output "app_url" {
  description = "Base URL of the rehosted legacy-portal"
  value       = "http://${aws_instance.app.public_dns}:8095"
}

output "rds_endpoint" {
  description = "Endpoint of the legacy-portal RDS PostgreSQL instance"
  value       = aws_db_instance.legacy_portal.endpoint
}

output "artifact_bucket" {
  description = "S3 bucket the deploy script uploads the fat JAR to"
  value       = aws_s3_bucket.artifacts.bucket
}

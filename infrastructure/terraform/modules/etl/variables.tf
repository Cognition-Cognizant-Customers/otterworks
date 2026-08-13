variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
}

variable "image_uri" {
  description = "ECR image URI (with tag) for the ETL Lambda container image built from etl-serverless/Dockerfile"
  type        = string
}

variable "data_lake_bucket_name" {
  description = "S3 data lake bucket name"
  type        = string
}

variable "data_lake_bucket_arn" {
  description = "S3 data lake bucket ARN"
  type        = string
}

variable "file_bucket_name" {
  description = "S3 file storage bucket name"
  type        = string
}

variable "file_bucket_arn" {
  description = "S3 file storage bucket ARN"
  type        = string
}

variable "audit_archive_bucket_name" {
  description = "S3 audit archive bucket name"
  type        = string
}

variable "audit_archive_bucket_arn" {
  description = "S3 audit archive bucket ARN"
  type        = string
}

variable "analytics_queue_url" {
  description = "SQS analytics events queue URL"
  type        = string
}

variable "analytics_queue_arn" {
  description = "SQS analytics events queue ARN"
  type        = string
}

variable "audit_events_table_name" {
  description = "DynamoDB audit events table name"
  type        = string
}

variable "audit_events_table_arn" {
  description = "DynamoDB audit events table ARN"
  type        = string
}

variable "file_metadata_table_name" {
  description = "DynamoDB file metadata table name"
  type        = string
}

variable "file_metadata_table_arn" {
  description = "DynamoDB file metadata table ARN"
  type        = string
}

variable "analytics_events_table_name" {
  description = "DynamoDB analytics events table name"
  type        = string
  default     = "otterworks-analytics-events"
}

variable "vpc_id" {
  description = "VPC ID for Lambdas that need in-VPC access (RDS, MeiliSearch, internal services)"
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet IDs for VPC-attached Lambdas"
  type        = list(string)
}

variable "document_service_url" {
  description = "Internal URL of the document service"
  type        = string
}

variable "file_service_url" {
  description = "Internal URL of the file service"
  type        = string
}

variable "meilisearch_url" {
  description = "Internal URL of MeiliSearch"
  type        = string
}

variable "analytics_prefix" {
  description = "S3 prefix for daily analytics partitions"
  type        = string
  default     = "analytics/daily"
}

variable "alert_email" {
  description = "Optional email address subscribed to the ETL alerts topic"
  type        = string
  default     = ""
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

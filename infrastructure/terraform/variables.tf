variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

variable "namespace" {
  description = "Kubernetes namespace for OtterWorks services"
  type        = string
  default     = "otterworks"
}

variable "db_password" {
  description = "Master password for the RDS PostgreSQL instance"
  type        = string
  sensitive   = true
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "meilisearch_master_key" {
  description = "MeiliSearch master key (required for production)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "etl_image_uri" {
  description = "ECR image URI (with tag) for the ETL Lambda container image built from etl-serverless/Dockerfile. Leave empty to skip provisioning the serverless ETL."
  type        = string
  default     = ""
}

variable "etl_document_service_url" {
  description = "Internal URL of the document service, reachable from the ETL Lambdas"
  type        = string
  default     = "http://document-service.otterworks.svc.cluster.local:8083"
}

variable "etl_file_service_url" {
  description = "Internal URL of the file service, reachable from the ETL Lambdas"
  type        = string
  default     = "http://file-service.otterworks.svc.cluster.local:8082"
}

variable "etl_meilisearch_url" {
  description = "Internal URL of MeiliSearch, reachable from the ETL Lambdas"
  type        = string
  default     = "http://meilisearch.otterworks.svc.cluster.local:7700"
}

variable "etl_alert_email" {
  description = "Optional email address subscribed to the ETL failure alerts SNS topic"
  type        = string
  default     = ""
}

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
  description = "URL of the document service reachable from the ETL Lambda ENIs (internal load balancer / internal ingress hostname, not a cluster-local DNS name). Required for the search-reindex pipeline. Prefer https:// where the endpoint supports TLS."
  type        = string
  default     = ""

  validation {
    condition     = var.etl_document_service_url == "" || can(regex("^https?://", var.etl_document_service_url))
    error_message = "etl_document_service_url must be a full URL including the http:// or https:// scheme."
  }
}

variable "etl_file_service_url" {
  description = "URL of the file service reachable from the ETL Lambda ENIs (internal load balancer / internal ingress hostname, not a cluster-local DNS name). Required for the search-reindex pipeline. Prefer https:// where the endpoint supports TLS."
  type        = string
  default     = ""

  validation {
    condition     = var.etl_file_service_url == "" || can(regex("^https?://", var.etl_file_service_url))
    error_message = "etl_file_service_url must be a full URL including the http:// or https:// scheme."
  }
}

variable "etl_meilisearch_url" {
  description = "URL of MeiliSearch reachable from the ETL Lambda ENIs (MeiliSearch runs on ECS; use its internal load balancer or service-discovery endpoint). Required for the search-reindex pipeline. Prefer https:// where the endpoint supports TLS."
  type        = string
  default     = ""

  validation {
    condition     = var.etl_meilisearch_url == "" || can(regex("^https?://", var.etl_meilisearch_url))
    error_message = "etl_meilisearch_url must be a full URL including the http:// or https:// scheme."
  }
}

variable "etl_alert_email" {
  description = "Optional email address subscribed to the ETL failure alerts SNS topic"
  type        = string
  default     = ""
}

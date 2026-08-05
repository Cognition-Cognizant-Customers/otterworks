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

variable "usage_rollup_lambda_jar_path" {
  description = "Path to the analytics-service assembly jar for the usage-rollup Lambda (build with `sbt assembly` in services/analytics-service)"
  type        = string
  default     = "../../services/analytics-service/target/scala-3.4.0/analytics-service-assembly-0.1.0.jar"
}

variable "meilisearch_master_key" {
  description = "MeiliSearch master key (required for production)"
  type        = string
  default     = ""
  sensitive   = true
}

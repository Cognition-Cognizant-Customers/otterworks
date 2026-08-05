variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

variable "project" {
  description = "Project name used as prefix for resource naming"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "Project name must be lowercase alphanumeric with hyphens, 2-21 characters."
  }
}

variable "lambda_jar_path" {
  description = "Path to the analytics-service assembly jar containing the usage-rollup Lambda handler (build with `sbt assembly` in services/analytics-service)"
  type        = string
}

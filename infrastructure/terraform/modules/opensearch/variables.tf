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
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}[a-z0-9]$", var.project))
    error_message = "Project name must be lowercase alphanumeric with hyphens (no trailing hyphen), 2-21 characters."
  }
}

variable "namespace" {
  description = "Namespace suffix applied to all resources so concurrent runs never collide (e.g. os-demo)"
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,7}[a-z0-9]$", var.namespace))
    error_message = "Namespace must be lowercase alphanumeric with hyphens (no trailing hyphen), 2-9 characters, so derived AOSS policy names stay within the 32-character limit."
  }
}

variable "access_principal_arns" {
  description = "IAM principal ARNs (roles/users) granted data access to the collection"
  type        = list(string)
}

variable "allow_public_access" {
  description = "Allow public network access to the collection endpoint (dev/demo only; keep false to restrict access to the given VPC endpoints)"
  type        = bool
  default     = false
}

variable "vpc_endpoint_ids" {
  description = "OpenSearch Serverless VPC endpoint IDs (required when allow_public_access = false)"
  type        = list(string)
  default     = []
}

variable "standby_replicas" {
  description = "Standby replicas setting for the collection (DISABLED halves OCU cost for dev/demo)"
  type        = string
  default     = "DISABLED"
}

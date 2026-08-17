variable "region" {
  description = "AWS region for the shared Cron Box resources"
  type        = string
  default     = "us-east-1"
}

variable "audit_retention_days" {
  description = "Audit event retention horizon enforced by DynamoDB TTL (cron-archive unit)"
  type        = number
  default     = 90
}

variable "audit_archive_prefix" {
  description = "S3 key prefix under which expiring audit events are archived"
  type        = string
  default     = "audit-archive/expired"
}

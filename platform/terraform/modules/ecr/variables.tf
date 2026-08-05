variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
}

variable "ecr_prefix" {
  description = "Prefix for ECR repository names"
  type        = string
  default     = "otterworks/"
}

variable "service_names" {
  description = "List of service names to create ECR repositories for"
  type        = list(string)
}

variable "golden_image_retention_count" {
  description = "Number of golden main-tagged images to retain. Only one image can carry the main tag, so any value >= 1 means the golden image is never expired."
  type        = number
  default     = 5
}

variable "tenant_image_retention_count" {
  description = "Number of tenant-* tagged images to retain per repository (upper bound on concurrent tenants whose images are protected)"
  type        = number
  default     = 100
}

variable "build_image_retention_count" {
  description = "Number of short-lived per-build <slug>-<sha> tagged images to retain per repository"
  type        = number
  default     = 10
}

variable "untagged_image_retention_count" {
  description = "Number of untagged images to retain per repository"
  type        = number
  default     = 10
}

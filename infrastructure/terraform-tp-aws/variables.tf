variable "aws_region" {
  description = "AWS region for the TP serverless demo stack"
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for every resource name (cost/teardown discipline)"
  type        = string
  default     = "ow-tp"
}

variable "report_tz" {
  description = "IANA timezone used to date-stamp the finance report filename (legacy job stamps with the ETL box's localtime)"
  type        = string
  default     = "UTC"
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the demo Lambdas"
  type        = number
  default     = 7
}

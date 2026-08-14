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

variable "log_retention_days" {
  description = "CloudWatch log retention for the demo Lambdas"
  type        = number
  default     = 7
}

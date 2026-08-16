variable "ns" {
  description = "Demo namespace; every resource name carries it (ow-tp-<ns>-...)"
  type        = string
  default     = "demo"

  validation {
    condition     = can(regex("^[a-z0-9]{1,12}$", var.ns))
    error_message = "ns must be 1-12 lowercase alphanumeric characters."
  }
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

locals {
  prefix = "ow-tp-${var.ns}"
}

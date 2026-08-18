variable "region" {
  description = "AWS region for the portal estate."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Resource name prefix (demo-account convention: ow-tp-)."
  type        = string
  default     = "ow-tp-portal"
}

variable "namespace" {
  description = "Run namespace. 'demo' is the persistent staging slice; anything else is a rehearsal namespace that must be destroyed after its run."
  type        = string
  default     = "demo"
}

variable "enable_demo_site" {
  description = "Host the Otter Portal demo page from an S3 static website."
  type        = bool
  default     = true
}

variable "devin_webhook_url" {
  description = "Optional Devin webhook endpoint for the alarm->Devin incident automation. Empty disables the EventBridge API destination."
  type        = string
  default     = ""
}

variable "devin_webhook_auth_header" {
  description = "Value for the Authorization header of the Devin webhook API destination (required when devin_webhook_url is set)."
  type        = string
  default     = "unused"
  sensitive   = true
}

variable "lambda_memory_mb" {
  description = "Memory size for the portal Lambdas."
  type        = number
  default     = 1024
}

variable "ns" {
  description = "Migration demo namespace."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_]{1,32}$", var.ns))
    error_message = "ns must contain only letters, digits, and underscores and be 1-32 characters."
  }
}

variable "project_id" {
  description = "Existing shared Atlas project ID."
  type        = string
  sensitive   = true
}

variable "public_key" {
  description = "Atlas API public key. Set from MONGODB_ATLAS_PUBLIC_KEY."
  type        = string
  sensitive   = true
}

variable "private_key" {
  description = "Atlas API private key. Set from MONGODB_ATLAS_PRIVATE_KEY."
  type        = string
  sensitive   = true
}

variable "cluster_name" {
  description = "Existing shared Atlas cluster name."
  type        = string
  default     = "otterworks-demo"
}

variable "caller_ip" {
  description = "Caller IPv4 address; when null, resolve it from api.ipify.org."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.caller_ip == null || can(cidrhost("${var.caller_ip}/32", 0))
    error_message = "caller_ip must be a valid IPv4 address."
  }
}

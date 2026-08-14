variable "prefix" {
  description = "Namespace prefix for every workspace object this stack creates (shared workspace guardrail)."
  type        = string
  default     = "ow_tp"

  validation {
    condition     = can(regex("^ow_tp", var.prefix))
    error_message = "All demo objects must keep the ow_tp prefix in the shared workspace."
  }
}

variable "catalog_name" {
  description = "Unity Catalog catalog holding the bronze/silver/gold schemas."
  type        = string
  default     = "ow_tp"
}

variable "warehouse_name" {
  description = "Name of the EXISTING serverless SQL warehouse to reuse (no compute is created by this stack)."
  type        = string
  default     = "Serverless Starter Warehouse"
}

# Replacements for the legacy estate's hardcoded credentials (etl/config.ini,
# plaintext mvsprod in the ksh job). Demo defaults are placeholders — override
# with TF_VAR_* env vars for a real deployment; values live only in the
# ow_tp secret scope, never in code.
variable "pg_password" {
  type      = string
  sensitive = true
  default   = "demo-placeholder"
}

variable "aws_access_key_id" {
  type      = string
  sensitive = true
  default   = "demo-placeholder"
}

variable "aws_secret_access_key" {
  type      = string
  sensitive = true
  default   = "demo-placeholder"
}

variable "meilisearch_api_key" {
  type      = string
  sensitive = true
  default   = "demo-placeholder"
}

variable "sftp_password" {
  type      = string
  sensitive = true
  default   = "demo-placeholder"
}

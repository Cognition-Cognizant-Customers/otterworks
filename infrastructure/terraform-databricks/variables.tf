variable "prefix" {
  description = "Demo prefix carried by every workspace object. Never change mid-run."
  type        = string
  default     = "ow_tp"

  validation {
    condition     = can(regex("^[a-z][a-z0-9_]*$", var.prefix))
    error_message = "prefix must be lowercase snake_case."
  }
}

variable "warehouse_name" {
  description = "Name of the EXISTING serverless SQL warehouse (never created here)."
  type        = string
  default     = "Serverless Starter Warehouse"
}

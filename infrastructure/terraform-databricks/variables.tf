variable "prefix" {
  description = "Prefix carried by every workspace-level object this stack creates. The demo workspace is shared, so the prefix is the isolation boundary and teardown filter."
  type        = string
  default     = "ow_tp"

  validation {
    condition     = can(regex("^ow_tp[a-z0-9_]*$", var.prefix))
    error_message = "prefix must match ^ow_tp[a-z0-9_]*$: the shared demo workspace is only safe to tear down by prefix."
  }
}

variable "catalog_name" {
  description = "Unity Catalog catalog holding the bronze/silver/gold schemas."
  type        = string
  default     = "ow_tp"

  validation {
    condition     = can(regex("^ow_tp[a-z0-9_]*$", var.catalog_name))
    error_message = "catalog_name must match ^ow_tp[a-z0-9_]*$."
  }
}

variable "warehouse_name" {
  description = "Name of the EXISTING serverless SQL warehouse to reuse. This stack never creates compute: no clusters, no warehouses, nothing with an hourly floor."
  type        = string
  default     = "Serverless Starter Warehouse"
}

# The legacy estate keeps its credentials in etl/config.ini and, in the ksh
# ingest job, inline in the source. The migration target is this secret scope;
# values are injected via TF_VAR_* and are never committed.
variable "secrets" {
  description = "Secret-scope contents replacing the legacy hardcoded credentials. Keys map 1:1 onto what the converted jobs read."
  type        = map(string)
  sensitive   = true
  default = {
    pg_password           = "demo-placeholder"
    aws_access_key_id     = "demo-placeholder"
    aws_secret_access_key = "demo-placeholder"
    meilisearch_api_key   = "demo-placeholder"
    sftp_password         = "demo-placeholder"
  }
}

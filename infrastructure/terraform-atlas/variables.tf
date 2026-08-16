variable "project_id" {
  description = "Atlas project id (otterworks-demos). Pass via TF_VAR_project_id from MONGODB_ATLAS_PROJECT_ID."
  type        = string
}

variable "cluster_name" {
  description = "Name of the pre-provisioned shared cluster. The cluster is NOT managed by this stack."
  type        = string
  default     = "otterworks-demo"
}

variable "namespace" {
  description = "Run namespace (NS). All run-scoped objects are named ow_tp_mongodb_<namespace>."
  type        = string
  default     = "demo"

  validation {
    condition     = can(regex("^[A-Za-z0-9_]+$", var.namespace))
    error_message = "namespace must fullmatch [A-Za-z0-9_]+."
  }
}

variable "vm_ip" {
  description = "Public IPv4 of the parent session VM to allow-list for the run. Empty string skips the entry."
  type        = string
  default     = ""
}

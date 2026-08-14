variable "project_id" {
  description = "Atlas project ID (project otterworks-demos). Set via TF_VAR_project_id or -var."
  type        = string
}

variable "cluster_name" {
  description = "Name of the demo cluster. The existing shared M0 cluster must be IMPORTED, never created (one M0 per project)."
  type        = string
  default     = "otterworks-demo"
}

variable "cluster_instance_size" {
  description = "Cluster tier. M0 (free, TENANT provider) by default; bump for larger demos."
  type        = string
  default     = "M0"
}

variable "cluster_region" {
  description = "Atlas region for the cluster (AWS)."
  type        = string
  default     = "US_EAST_1"
}

variable "mongodb_major_version" {
  description = "MongoDB major version of the cluster."
  type        = string
  default     = "8.0"
}

variable "demo_db_username" {
  description = "Database user for the migration demo."
  type        = string
  default     = "otterworks-demo-migrator"
}

variable "access_cidr" {
  description = "CIDR allowed to reach the cluster. 0.0.0.0/0 is demo-grade only — Devin VMs and demo laptops have no stable egress IPs."
  type        = string
  default     = "0.0.0.0/0"
}

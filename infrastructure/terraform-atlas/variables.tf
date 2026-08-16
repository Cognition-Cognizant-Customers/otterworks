variable "project_id" {
  description = "MongoDB Atlas project ID."
  type        = string
}

variable "ns" {
  description = "Migration namespace."
  type        = string
  default     = "demo"
}

variable "cluster_name" {
  description = "Atlas cluster name."
  type        = string
  default     = "otterworks-demo"
}

variable "cluster_tier" {
  description = "Atlas instance size tier used when managing a dedicated cluster."
  type        = string
  default     = "M0"
}

variable "region" {
  description = "Atlas cloud region."
  type        = string
  default     = "US_EAST_1"
}

variable "manage_cluster" {
  description = "Create and manage the cluster. Keep false for the shared existing M0 demo cluster."
  type        = bool
  default     = false
}

variable "access_cidr" {
  description = "CIDR block allowed to access Atlas, normally this VM's /32."
  type        = string
}

variable "db_password" {
  description = "Password for the per-run migration database user."
  type        = string
  sensitive   = true
}

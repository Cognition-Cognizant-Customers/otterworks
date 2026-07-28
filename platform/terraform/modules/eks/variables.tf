variable "project" {
  description = "Project name"
  type        = string
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
}

variable "cluster_name" {
  description = "Name for the EKS cluster"
  type        = string
}

variable "cluster_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.32"
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for the EKS cluster"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Private subnet IDs for the EKS cluster"
  type        = list(string)
}

variable "node_subnet_ids" {
  description = "Subnet IDs for the node group (typically public for cost-optimized dev)"
  type        = list(string)
}

variable "node_instance_types" {
  description = "Instance types for the managed node group"
  type        = list(string)
  default     = ["t3.large"]
}

variable "node_capacity_type" {
  description = "Capacity type: ON_DEMAND or SPOT"
  type        = string
  default     = "ON_DEMAND"
}

variable "node_desired_size" {
  description = "Desired number of nodes"
  type        = number
  default     = 2
}

variable "node_min_size" {
  description = "Minimum number of nodes"
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum number of nodes"
  type        = number
  default     = 4
}

variable "ebs_csi_driver_version" {
  description = "Version of the EBS CSI driver addon"
  type        = string
  default     = "v1.37.0-eksbuild.1"
}

variable "enable_karpenter" {
  description = "Provision the AWS side of Karpenter (controller role, node instance profile, interruption queue, discovery tags)."
  type        = bool
  default     = true
}

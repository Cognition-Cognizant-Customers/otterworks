variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be one of: dev, staging, prod."
  }
}

variable "instance_type" {
  description = "EC2 instance type for the legacy-portal VM"
  type        = string
  default     = "t3.small"
}

variable "db_instance_class" {
  description = "RDS instance class for the legacy-portal database"
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS allocated storage in GiB"
  type        = number
  default     = 20
}

variable "artifact_key" {
  description = "S3 key of the legacy-portal fat JAR in the artifact bucket"
  type        = string
  default     = "legacy-portal.jar"
}

variable "ami_id" {
  description = "Pin the EC2 AMI to a specific ID. Defaults to the latest AL2023 image at first apply; later AL2023 releases never replace the instance (ami changes are ignored)."
  type        = string
  default     = null
}

variable "app_ingress_cidr_blocks" {
  description = "CIDR blocks allowed to reach legacy-portal on port 8095. Empty by default (no ingress): the app serves unauthenticated plain-HTTP endpoints, so explicitly set a trusted CIDR to open access."
  type        = list(string)
  default     = []
}

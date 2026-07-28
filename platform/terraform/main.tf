# ------------------------------------------------------------------------------
# OtterWorks Platform - Root Module
# Provisions standalone VPC, EKS cluster, and ECR repositories
# No dependency on platform-engineering shared services
# ------------------------------------------------------------------------------

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  backend "s3" {
    bucket = "otterworks-terraform-state"
    key    = "platform/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "otterworks"
      Environment = var.environment
      ManagedBy   = "terraform"
      Layer       = "platform"
    }
  }
}

# --- VPC ---

module "vpc" {
  source = "./modules/vpc"

  project            = "otterworks"
  environment        = var.environment
  vpc_cidr           = var.vpc_cidr
  az_count           = var.az_count
  cluster_name       = var.cluster_name
  enable_nat_gateway = var.enable_nat_gateway
}

# --- EKS ---

module "eks" {
  source = "./modules/eks"

  project         = "otterworks"
  environment     = var.environment
  cluster_name    = var.cluster_name
  cluster_version = var.cluster_version

  public_subnet_ids  = module.vpc.public_subnet_ids
  private_subnet_ids = module.vpc.private_subnet_ids
  # Use public subnets for nodes in dev (no NAT gateway needed, cost-optimized)
  node_subnet_ids = var.enable_nat_gateway ? module.vpc.private_subnet_ids : module.vpc.public_subnet_ids

  node_instance_types = var.node_instance_types
  node_capacity_type  = var.node_capacity_type
  node_desired_size   = var.node_desired_size
  node_min_size       = var.node_min_size
  node_max_size       = var.node_max_size
}

# --- ECR ---

module "ecr" {
  source = "./modules/ecr"

  project     = "otterworks"
  environment = var.environment
  ecr_prefix  = var.ecr_prefix

  service_names = [
    "api-gateway",
    "auth-service",
    "file-service",
    "document-service",
    "collab-service",
    "notification-service",
    "search-service",
    "analytics-service",
    "admin-service",
    "audit-service",
    "report-service",
    "web-app",
    "admin-dashboard",
  ]
}

terraform {
  required_version = ">= 1.15.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "otterworks-tp"
      Track     = "aws-serverless"
      Namespace = var.ns
      ManagedBy = "terraform"
    }
  }
}

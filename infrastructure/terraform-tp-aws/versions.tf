terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Deliberately local state, separate from the main stack
  # (infrastructure/terraform uses the otterworks-terraform-state S3 backend).
  # *.tfstate and .terraform/ are gitignored repo-wide.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "otterworks-tp"
      Track     = "tech-partnerships"
      ManagedBy = "terraform"
      Stack     = "terraform-tp-aws"
    }
  }
}

terraform {
  required_version = ">= 1.10.0"

  # Remote namespace-keyed state. Partial config on purpose — the parent run
  # initializes it (never hardcode a namespace here):
  #   terraform init \
  #     -backend-config="bucket=otterworks-terraform-state" \
  #     -backend-config="key=tp-portal/<namespace>/terraform.tfstate" \
  #     -backend-config="region=us-east-1" \
  #     -backend-config="use_lockfile=true"
  # Native S3 locking (use_lockfile) — no DynamoDB lock table.
  # Fixture runs init with an uncommitted local backend override file instead.
  backend "s3" {}

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

# ------------------------------------------------------------------------------
# OtterWorks Infrastructure - Application-Specific AWS Resources
# Platform (VPC, EKS, ECR) is provisioned by /platform/terraform
# This layer provisions app-level resources: RDS, DynamoDB, S3, SQS, etc.
# ------------------------------------------------------------------------------

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.27"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
  }

  backend "s3" {
    bucket = "otterworks-terraform-state"
    key    = "otterworks/terraform.tfstate"
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
      Layer       = "application"
    }
  }
}

# --- Read Platform Outputs (VPC, EKS, ECR from /platform/terraform) ---

data "terraform_remote_state" "platform" {
  backend = "s3"

  config = {
    bucket = "otterworks-terraform-state"
    key    = "platform/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  cluster_name      = data.terraform_remote_state.platform.outputs.cluster_name
  cluster_endpoint  = data.terraform_remote_state.platform.outputs.cluster_endpoint
  cluster_ca        = data.terraform_remote_state.platform.outputs.cluster_certificate_authority
  vpc_id            = data.terraform_remote_state.platform.outputs.vpc_id
  vpc_cidr          = data.terraform_remote_state.platform.outputs.vpc_cidr_block
  private_subnets   = data.terraform_remote_state.platform.outputs.private_subnet_ids
  public_subnets    = data.terraform_remote_state.platform.outputs.public_subnet_ids
  oidc_provider_arn = data.terraform_remote_state.platform.outputs.oidc_provider_arn
  oidc_provider_url = data.terraform_remote_state.platform.outputs.oidc_provider_url
}

data "aws_eks_cluster_auth" "platform" {
  name = local.cluster_name
}

provider "kubernetes" {
  host                   = local.cluster_endpoint
  cluster_ca_certificate = base64decode(local.cluster_ca)
  token                  = data.aws_eks_cluster_auth.platform.token
}

provider "helm" {
  kubernetes {
    host                   = local.cluster_endpoint
    cluster_ca_certificate = base64decode(local.cluster_ca)
    token                  = data.aws_eks_cluster_auth.platform.token
  }
}

# --- Modules ---

module "storage" {
  source      = "./modules/storage"
  environment = var.environment
  project     = "otterworks"
}

module "database" {
  source      = "./modules/database"
  environment = var.environment
  project     = "otterworks"
  db_password = var.db_password

  vpc_id     = local.vpc_id
  vpc_cidr   = local.vpc_cidr
  subnet_ids = local.private_subnets
}

module "messaging" {
  source      = "./modules/messaging"
  environment = var.environment
  project     = "otterworks"
}

module "search" {
  source      = "./modules/search"
  environment = var.environment
  project     = "otterworks"

  vpc_id                 = local.vpc_id
  vpc_cidr               = local.vpc_cidr
  subnet_ids             = local.private_subnets
  meilisearch_master_key = var.meilisearch_master_key
}

module "auth" {
  source      = "./modules/auth"
  environment = var.environment
  project     = "otterworks"
}

module "cache" {
  source              = "./modules/cache"
  environment         = var.environment
  project             = "otterworks"
  vpc_id              = local.vpc_id
  subnet_ids          = local.private_subnets
  allowed_cidr_blocks = [local.vpc_cidr]
}

module "monitoring" {
  source             = "./modules/monitoring"
  environment        = var.environment
  project            = "otterworks"
  log_retention_days = var.log_retention_days
}

module "etl" {
  source      = "./modules/etl"
  count       = var.etl_image_uri == "" ? 0 : 1
  environment = var.environment
  project     = "otterworks"

  image_uri = var.etl_image_uri

  data_lake_bucket_name     = module.storage.data_lake_bucket_name
  data_lake_bucket_arn      = module.storage.data_lake_bucket_arn
  file_bucket_name          = module.storage.file_bucket_name
  file_bucket_arn           = module.storage.file_bucket_arn
  audit_archive_bucket_name = module.storage.audit_archive_bucket_name
  audit_archive_bucket_arn  = module.storage.audit_archive_bucket_arn

  analytics_queue_url = module.messaging.analytics_queue_url
  analytics_queue_arn = module.messaging.analytics_queue_arn

  audit_events_table_name  = module.database.audit_events_table_name
  audit_events_table_arn   = module.database.audit_events_table_arn
  file_metadata_table_name = module.database.file_metadata_table_name
  file_metadata_table_arn  = module.database.file_metadata_table_arn

  vpc_id     = local.vpc_id
  vpc_cidr   = local.vpc_cidr
  subnet_ids = local.private_subnets

  document_service_url = var.etl_document_service_url
  file_service_url     = var.etl_file_service_url
  meilisearch_url      = var.etl_meilisearch_url
  alert_email          = var.etl_alert_email
  log_retention_days   = var.log_retention_days
}

# --- MeiliSearch is deployed via ECS; no domain access policy needed. ---

module "irsa" {
  source            = "./modules/irsa"
  environment       = var.environment
  project           = "otterworks"
  namespace         = var.namespace
  oidc_provider_arn = local.oidc_provider_arn
  oidc_provider_url = local.oidc_provider_url

  service_accounts = {
    "file-service" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "s3:GetObject",
            "s3:PutObject",
            "s3:DeleteObject",
            "s3:ListBucket",
          ]
          Resource = [
            module.storage.file_bucket_arn,
            "${module.storage.file_bucket_arn}/*",
          ]
        },
        {
          Effect = "Allow"
          Action = [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
            "dynamodb:DeleteItem",
            "dynamodb:Query",
            "dynamodb:Scan",
          ]
          Resource = [
            module.database.file_metadata_table_arn,
            "${module.database.file_metadata_table_arn}/index/*",
            module.database.folders_table_arn,
            "${module.database.folders_table_arn}/index/*",
            module.database.file_versions_table_arn,
            "${module.database.file_versions_table_arn}/index/*",
            module.database.file_shares_table_arn,
            "${module.database.file_shares_table_arn}/index/*",
          ]
        },
      ]
    })

    "document-service" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "s3:GetObject",
            "s3:PutObject",
          ]
          Resource = [
            module.storage.file_bucket_arn,
            "${module.storage.file_bucket_arn}/*",
          ]
        },
        {
          Effect   = "Allow"
          Action   = ["sns:Publish"]
          Resource = [module.messaging.events_topic_arn]
        },
      ]
    })

    "notification-service" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "sqs:ReceiveMessage",
            "sqs:DeleteMessage",
            "sqs:GetQueueAttributes",
          ]
          Resource = [module.messaging.notification_queue_arn]
        },
        {
          Effect = "Allow"
          Action = [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:Query",
          ]
          Resource = [
            module.database.notifications_table_arn,
            "${module.database.notifications_table_arn}/index/*",
          ]
        },
        {
          Effect   = "Allow"
          Action   = ["ses:SendEmail", "ses:SendRawEmail"]
          Resource = ["*"]
        },
      ]
    })

    "search-service" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "sqs:ReceiveMessage",
            "sqs:DeleteMessage",
            "sqs:GetQueueAttributes",
          ]
          Resource = [module.messaging.search_indexing_queue_arn]
        },
        {
          Effect = "Allow"
          Action = [
            "ecs:DescribeServices",
            "ecs:DescribeTasks",
          ]
          Resource = [
            module.search.meilisearch_ecs_cluster_arn,
            module.search.meilisearch_ecs_service_arn,
            "${replace(module.search.meilisearch_ecs_cluster_arn, ":cluster/", ":task/")}/*",
          ]
        },
      ]
    })

    "analytics-service" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "sqs:ReceiveMessage",
            "sqs:DeleteMessage",
            "sqs:GetQueueAttributes",
          ]
          Resource = [module.messaging.analytics_queue_arn]
        },
        {
          Effect = "Allow"
          Action = [
            "s3:PutObject",
            "s3:GetObject",
            "s3:ListBucket",
          ]
          Resource = [
            module.storage.data_lake_bucket_arn,
            "${module.storage.data_lake_bucket_arn}/*",
          ]
        },
      ]
    })

    "audit-service" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "dynamodb:PutItem",
            "dynamodb:GetItem",
            "dynamodb:Query",
            "dynamodb:BatchWriteItem",
          ]
          Resource = [
            module.database.audit_events_table_arn,
            "${module.database.audit_events_table_arn}/index/*",
          ]
        },
        {
          Effect = "Allow"
          Action = [
            "s3:PutObject",
          ]
          Resource = [
            module.storage.audit_archive_bucket_arn,
            "${module.storage.audit_archive_bucket_arn}/*",
          ]
        },
      ]
    })

    "auth-service" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "cognito-idp:AdminCreateUser",
            "cognito-idp:AdminGetUser",
            "cognito-idp:AdminUpdateUserAttributes",
            "cognito-idp:AdminDisableUser",
            "cognito-idp:AdminEnableUser",
            "cognito-idp:ListUsers",
          ]
          Resource = [module.auth.user_pool_arn]
        },
      ]
    })

    "admin-service" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "cognito-idp:ListUsers",
            "cognito-idp:AdminGetUser",
            "cognito-idp:AdminDisableUser",
            "cognito-idp:AdminEnableUser",
          ]
          Resource = [module.auth.user_pool_arn]
        },
        {
          Effect   = "Allow"
          Action   = ["cloudwatch:GetMetricData", "cloudwatch:ListMetrics"]
          Resource = ["*"]
        },
      ]
    })

    "api-gateway" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect   = "Allow"
          Action   = ["sns:Publish"]
          Resource = [module.messaging.events_topic_arn]
        },
      ]
    })

    "collab-service" = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect   = "Allow"
          Action   = ["sns:Publish"]
          Resource = [module.messaging.events_topic_arn]
        },
      ]
    })
  }
}

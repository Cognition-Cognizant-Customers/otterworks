# ------------------------------------------------------------------------------
# OtterWorks Platform - ECR Module
# Container registries for all microservices
# ------------------------------------------------------------------------------

locals {
  common_tags = {
    Module  = "ecr"
    Project = var.project
  }
}

resource "aws_ecr_repository" "services" {
  for_each = toset(var.service_names)

  name                 = "${var.ecr_prefix}${each.value}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.environment == "dev"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.common_tags, {
    Service = each.value
  })
}

resource "aws_ecr_lifecycle_policy" "services" {
  for_each = aws_ecr_repository.services

  repository = each.value.name

  # Rules are evaluated lowest rulePriority first, and an image is only ever
  # acted on by the first rule that matches it. The `main` and `tenant-*`
  # rules therefore shield the golden and per-tenant images from the
  # catch-all build rule below them; their retention counts are high enough
  # that they never fire in practice while still bounding storage.
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Retain the golden main image (never expired in practice)"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["main"]
          countType     = "imageCountMoreThan"
          countNumber   = var.golden_image_retention_count
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Retain per-tenant tenant-* images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["tenant-"]
          countType     = "imageCountMoreThan"
          countNumber   = var.tenant_image_retention_count
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 3
        description  = "Expire short-lived per-build <slug>-<sha> images beyond the retention count"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = var.build_image_retention_count
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 4
        description  = "Expire untagged images"
        selection = {
          tagStatus   = "untagged"
          countType   = "imageCountMoreThan"
          countNumber = var.untagged_image_retention_count
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

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

  # Golden images must survive pruning: every tenant without its own build of a
  # service falls back to `main`, and losing it made deploy-tenant.sh ship
  # another tenant's demo build into the perpetual t-main environment. The
  # floating `main` / `tenant-*` tags match no rule and are retained; branch
  # builds, per-commit golden builds and untagged manifests are pruned with
  # bounded keep counts.
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged manifests after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      },
      # Multiple entries in a tagPatternList are ANDed by ECR (an image must
      # carry tags matching every pattern), so each pattern gets its own rule.
      # Age-based, not count-based: imageCountMoreThan counts matches across
      # the whole repository, so a busy workshop's pushes would evict another
      # live attendee's current build. 14 days comfortably exceeds the 72h
      # tenant TTL, so an image can only expire after its tenant is gone.
      {
        rulePriority = 2
        description  = "Expire demo branch builds after 14 days (incl. TENANT_PREFIX-ed fork tags)"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*demo-*"]
          countType      = "sinceImagePushed"
          countUnit      = "days"
          countNumber    = 14
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 3
        description  = "Expire workshop branch builds after 14 days (incl. TENANT_PREFIX-ed fork tags)"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*workshop-*"]
          countType      = "sinceImagePushed"
          countUnit      = "days"
          countNumber    = 14
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 4
        description  = "Keep last 30 per-commit golden builds (incl. TENANT_PREFIX-ed fork tags)"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*main-*"]
          countType      = "imageCountMoreThan"
          countNumber    = 30
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

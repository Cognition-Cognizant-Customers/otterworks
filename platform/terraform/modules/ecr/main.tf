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

  # Never expire golden images (main / main-<sha> / tenant-main): every tenant
  # without its own build of a service falls back to `main`, and losing it made
  # deploy-tenant.sh ship another tenant's demo build into the perpetual t-main
  # environment. Only branch/demo builds and untagged manifests are pruned;
  # images matching no rule are retained.
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
      {
        rulePriority = 2
        description  = "Keep last 20 demo/workshop branch builds"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["demo-*", "workshop-*"]
          countType      = "imageCountMoreThan"
          countNumber    = 20
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

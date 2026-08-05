# Asserts the rendered ECR lifecycle policy never expires the golden `main`
# tag or the per-tenant `tenant-*` tags, and that untagged and per-build
# <slug>-<sha> images are the only ones expired.
#
# Run with:
#   cd platform/terraform/modules/ecr && terraform init -backend=false && terraform test

mock_provider "aws" {}

variables {
  project       = "otterworks"
  environment   = "dev"
  service_names = ["api-gateway"]
}

run "lifecycle_policy_protects_golden_and_tenant_tags" {
  command = plan

  # No tagStatus "any" rule may exist: an "any" rule matches main and
  # tenant-* images, which is exactly the bug this policy fixes.
  assert {
    condition = length([
      for r in jsondecode(aws_ecr_lifecycle_policy.services["api-gateway"].policy).rules :
      r if r.selection.tagStatus == "any"
    ]) == 0
    error_message = "Policy must not contain a tagStatus \"any\" rule; it would expire the golden main and tenant-* images"
  }

  # The main rule must be the highest-precedence rule (lowest rulePriority),
  # so the golden image is claimed by it before any expiring rule can match.
  assert {
    condition = anytrue([
      for r in jsondecode(aws_ecr_lifecycle_policy.services["api-gateway"].policy).rules :
      try(r.selection.tagPrefixList, []) == ["main"] &&
      r.rulePriority == min([
        for p in jsondecode(aws_ecr_lifecycle_policy.services["api-gateway"].policy).rules : p.rulePriority
      ]...) &&
      r.selection.countNumber >= 1
    ])
    error_message = "The main tag rule must have the lowest rulePriority and retain at least one image"
  }

  # The tenant-* rule must outrank every rule that is not the main rule, so
  # tenant images are claimed before the catch-all build rule can match them.
  assert {
    condition = anytrue([
      for r in jsondecode(aws_ecr_lifecycle_policy.services["api-gateway"].policy).rules :
      try(r.selection.tagPrefixList, []) == ["tenant-"] &&
      r.selection.countNumber >= 1 &&
      alltrue([
        for other in jsondecode(aws_ecr_lifecycle_policy.services["api-gateway"].policy).rules :
        r.rulePriority < other.rulePriority
        if try(other.selection.tagPrefixList, []) != ["tenant-"] && try(other.selection.tagPrefixList, []) != ["main"]
      ])
    ])
    error_message = "The tenant-* rule must outrank all expiring rules"
  }

  # A rule must still expire the short-lived <slug>-<sha> build tags: a
  # tagged catch-all that only sees images not already claimed above.
  assert {
    condition = anytrue([
      for r in jsondecode(aws_ecr_lifecycle_policy.services["api-gateway"].policy).rules :
      r.selection.tagStatus == "tagged" &&
      try(r.selection.tagPatternList, []) == ["*"] &&
      r.action.type == "expire"
    ])
    error_message = "Policy must expire leftover tagged per-build <slug>-<sha> images"
  }

  # Untagged images must be expired.
  assert {
    condition = anytrue([
      for r in jsondecode(aws_ecr_lifecycle_policy.services["api-gateway"].policy).rules :
      r.selection.tagStatus == "untagged" && r.action.type == "expire"
    ])
    error_message = "Policy must expire untagged images"
  }

  # ECR requires rulePriority values to be unique.
  assert {
    condition = length(distinct([
      for r in jsondecode(aws_ecr_lifecycle_policy.services["api-gateway"].policy).rules : r.rulePriority
    ])) == length(jsondecode(aws_ecr_lifecycle_policy.services["api-gateway"].policy).rules)
    error_message = "rulePriority values must be unique"
  }
}

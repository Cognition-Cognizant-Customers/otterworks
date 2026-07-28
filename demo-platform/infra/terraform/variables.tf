variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "cluster_name" {
  type    = string
  default = "otterworks-dev"
}

variable "platform_namespace" {
  type    = string
  default = "otterworks-platform"
}

variable "dashboard_service_account" {
  type    = string
  default = "demo-ops-dashboard"
}

variable "control_table_name" {
  type    = string
  default = "otterworks-demo-control"
}

# This gate existed only while the domain was unregistered. It now defaults on,
# because every tenant URL and the wildcard certificate depend on the IRSA role
# it creates: with the old default, an apply that simply forgot
# `-var enable_dns=true` deleted that role out from under external-dns and
# cert-manager, leaving tenant DNS and certificate renewal broken while the
# existing records made it look fine.
#
# Safe to default on now that the hosted zone is a data lookup (see dns.tf) --
# turning this off tears down the automation role, never the zone.
variable "enable_dns" {
  type    = bool
  default = true
}

variable "dns_zone_name" {
  type    = string
  default = "otterworks.app"
}

# Shared data-plane resources the reaper must be able to GC per-tenant slices of.
# Prefix match keeps the policy stable as tables/buckets are added.
variable "shared_dynamodb_table_prefix" {
  type    = string
  default = "otterworks-"
}

variable "shared_s3_bucket_prefix" {
  type    = string
  default = "otterworks-"
}

# S3 bucket holding the application Terraform state; the runner reads it via
# `terraform output` (load_infra_outputs) to resolve RDS/S3/DynamoDB coordinates.
variable "terraform_state_bucket" {
  type    = string
  default = "otterworks-terraform-state"
}

# ECR repository namespace for the app service images; deploy-tenant.sh resolves
# the newest tag per service via ecr:DescribeImages.
variable "ecr_repo_prefix" {
  type    = string
  default = "otterworks"
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget for the otterworks platform, in USD. See demo-platform/docs/cost-and-scale.md for how this figure is derived."
  type        = number
  default     = 700
}

variable "budget_alert_emails" {
  description = "Addresses notified when spend crosses the budget thresholds. No budget is created when empty."
  type        = list(string)
  default     = []
}

# Seed values for the CONFIG#reaper control item. These apply at install only --
# the dashboard owns the item from then on (see control_table.tf).
variable "reaper_enabled" {
  description = "Whether the reaper acts on its schedule. Off means no TTL reaping and no idle suspension."
  type        = bool
  default     = true
}

variable "reaper_schedule_cron" {
  description = "Cron schedule the reaper CronJob runs on."
  type        = string
  default     = "*/15 * * * *"
}

variable "reaper_grace_seconds" {
  description = "Extra grace beyond a tenant's expires_at before it is reaped."
  type        = number
  default     = 300
}

variable "reaper_idle_after_seconds" {
  description = "Zero ingress requests for this long scales a tenant to zero. Its namespace, config and database survive."
  type        = number
  default     = 3600
}

# Whose leftovers the infrastructure sweep may delete. Mirrored into the reaper
# as SWEEPABLE_CLUSTERS and into the IAM conditions in iam_dashboard.tf, so the
# script and the role agree on ownership. Add a name here when a cluster is
# replaced under a new name and its orphans still need reclaiming; a cluster
# absent from this list is another team's problem, not ours.
#
# Empty means "just this platform's own cluster" -- see local.sweepable_clusters.
variable "sweepable_clusters" {
  description = "Extra EKS cluster names whose orphaned AWS resources the sweep may delete, beyond cluster_name."
  type        = list(string)
  default     = []
}

variable "provisioner_user_name" {
  description = "IAM user who provisions demo tenants through the dashboard (holds only the passcode grant)."
  type        = string
  default     = "de-demo-provisioner"
}

variable "enable_github_actions_role" {
  description = "Create the OIDC role GitHub Actions assumes for continuous delivery. Requires the GitHub OIDC provider to already exist in the account."
  type        = bool
  default     = true
}

# owner/repo => the branch patterns (git ref names, may contain *) whose workflow
# runs in that repository may assume the CD role.
#
# Downstream forks ship the same environments from the same registry, so they
# are trusted too -- but only for the ephemeral branch prefixes. `main` is the
# golden app and the perpetual environment, which this repository owns: a fork's
# main is a copy of it, and deploying that copy would let anyone with write
# access to a fork replace the environment everyone else demos from.
variable "github_actions_trusted_repos" {
  description = "Repositories (owner/repo) and the branch patterns whose workflow runs may assume the CD role."
  type        = map(list(string))
  default = {
    "Cognition-Partner-Workshops/otterworks" = ["main", "workshop-*", "demo-*"]
    "COG-GTM/otterworks"                     = ["workshop-*", "demo-*"]
  }
}


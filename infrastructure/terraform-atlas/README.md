# Terraform — MongoDB Atlas (tech-partnerships demo)

Self-contained stack managing the Atlas-side configuration for the MongoDB
modernization demo (`docs/tech-partnerships/runbook-mongodb.md`):

- the project IP access list entry (`0.0.0.0/0`, demo-grade — Devin VMs and
  demo laptops have no stable egress IPs),
- a dedicated database user (`otterworks-demo-migrator`, generated password),
- the shared `otterworks-demo` M0 cluster — **imported, never created** (Atlas
  allows one M0 per project).

State is local and gitignored (repo-wide `*.tfstate` / `.terraform/` rules).

## Prerequisites

Environment variables (org secrets — never committed):

```bash
export MONGODB_ATLAS_PUBLIC_KEY=...    # programmatic API key
export MONGODB_ATLAS_PRIVATE_KEY=...
export TF_VAR_project_id="$MONGODB_ATLAS_PROJECT_ID"
```

## Apply

```bash
cd infrastructure/terraform-atlas
terraform init

# One-time: adopt the existing shared M0 cluster instead of creating one
terraform import mongodbatlas_advanced_cluster.demo "${TF_VAR_project_id}-otterworks-demo"

terraform apply
```

The apply creates the access-list entry and the demo user, and reconciles the
imported cluster (tier is `var.cluster_instance_size`, default `M0`).

Read the generated demo-user password (for `mongodb+srv://` URIs):

```bash
terraform output -raw demo_db_password
```

## Destroy (teardown)

The `otterworks-demo` M0 cluster is **shared** across demos. A plain
`terraform destroy` would delete it (it is blocked by `prevent_destroy`, which
fails the destroy). Remove the cluster from state first, then destroy the
rest (access-list entry + demo user):

```bash
terraform state rm mongodbatlas_advanced_cluster.demo
terraform destroy
```

This removes the Terraform-managed access entry and the demo database user
while leaving the shared cluster untouched. Re-adopt the cluster later with
the `terraform import` command above.

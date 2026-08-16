# Shared Databricks estate (tech-partnerships migration demo)

Parent-owned Terraform for the shared `ow_tp` estate:

- catalog `ow_tp` with medallion schemas `bronze` / `silver` / `gold`
- managed landing volume `/Volumes/ow_tp/bronze/landing`
- secret scope `ow_tp`
- notebook root `/Shared/ow_tp`
- reference to the EXISTING serverless SQL warehouse (`data` source — this stack
  never creates compute and never creates anything with an hourly cost)

## Ownership rules

- **Only the parent orchestration session runs `terraform apply` / `destroy`.**
  Children never hold or mutate this state.
- Children contribute their converted job as a new `jobs_<unit>.tf` file only,
  guarded by the same `ow_tp` prefix; they must not edit `main.tf`,
  `variables.tf`, `versions.tf`, or `outputs.tf`.
- No DDL against shared tables from any child. Per-unit tables are created by
  the unit's own job in its own namespace slice.
- All demo data is per-namespace (`ns=demo` for a live run) and destroyed after
  the run (`make dbx-destroy && make dbx-verify-teardown`).

## Usage (parent only)

```bash
export DATABRICKS_HOST="$DATABRICKS_DEMO_HOST"
export DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
cd infrastructure/terraform-databricks
terraform init
terraform plan -detailed-exitcode
terraform apply
```

Teardown is verified negatively: `make dbx-verify-teardown` scans the workspace
for any leftover `ow_tp`-prefixed object and fails if one exists.

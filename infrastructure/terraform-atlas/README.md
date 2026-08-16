# OtterWorks MongoDB Atlas stack

This parent-owned stack creates the per-run Atlas database user and IP access
entry used by the MongoDB migration demo.

The demo path leaves the existing `otterworks-demo` M0 cluster untouched and
reads it through a data source. Set `manage_cluster=true` for a full-scale run
that needs Terraform to create a dedicated cluster.

Credentials are supplied through environment variables:

```bash
export MONGODB_ATLAS_PUBLIC_KEY=...
export MONGODB_ATLAS_PRIVATE_KEY=...
export MONGODB_ATLAS_PROJECT_ID=...
export TF_VAR_project_id="$MONGODB_ATLAS_PROJECT_ID"
export TF_VAR_access_cidr="$(curl -fsS https://api.ipify.org)/32"
export TF_VAR_db_password='use-a-secret-manager-or-shell-environment'
```

Do not put credentials in Terraform files, tfvars files, or committed state.
Local state and tfvars files are ignored by this directory.

For the demo cluster:

```bash
terraform init
terraform apply \
  -var='manage_cluster=false' \
  -var='cluster_tier=M0'
```

The cluster is intentionally not managed in this mode because Atlas refuses
updates to M0/M2/M5 tenant clusters, and replacing the existing M0 would
change the SRV hostname stored by the migration track.

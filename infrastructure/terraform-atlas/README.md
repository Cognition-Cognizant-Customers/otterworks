# terraform-atlas — parent-owned, run-scoped Atlas objects (MongoDB track)

Applied and destroyed ONLY by the parent orchestration session. Children never
run terraform here, never touch the project access list, and never create
shared objects.

The shared M0 cluster `otterworks-demo` and the `otterworks-app` user are
pre-provisioned project infrastructure and are consumed as a **data source**:
`terraform destroy` of this stack removes only the run user and the run's
access-list entry, never the cluster.

```bash
export TF_VAR_project_id="$MONGODB_ATLAS_PROJECT_ID"
export TF_VAR_vm_ip="$(curl -s https://api.ipify.org)"   # parent VM only
terraform init
terraform apply    # creates ow_tp_mongodb_<ns> user + access-list entry
terraform destroy  # teardown; verify negatively afterwards (user + entry gone)
```

Target namespace layout on the cluster (created by migration code, dropped at
teardown): database `ow_tp_mongodb_<ns>` with collections `customers`,
`invoices`, `documents`, `files`, and `ow_tp_mongodb_<ns>_quarantine` for
quarantined records.

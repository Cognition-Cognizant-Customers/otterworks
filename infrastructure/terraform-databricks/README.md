# terraform-databricks — OtterWorks lakehouse demo estate

Self-contained Terraform stack for the tech-partnerships Databricks track.
It stands up the entire "after" state of the legacy-ETL migration —
Unity Catalog `ow_tp` catalog with `bronze`/`silver`/`gold` schemas, a managed
landing volume, the `ow_tp` secret scope, and two serverless Workflows jobs
(`ow_tp_custbill_lakehouse`, `ow_tp_python_etl_wave`) — and tears it all down
with one command.

Guardrails for the shared demo workspace:

- **Everything is `ow_tp`-prefixed.** The stack never reads or writes any
  object without that prefix.
- **No compute is created.** Jobs run on serverless jobs compute; queries reuse
  the existing serverless SQL warehouse (`warehouse_name` variable, discovered
  via a data source). Cost is per-run only — idle cost is zero.
- **State is local and gitignored.** This is a disposable demo estate; do not
  add a remote backend.

Two workspace quirks the stack works around:

- **Default Storage workspace:** the catalogs API refuses to create catalogs
  (`Metastore storage root URL does not exist`), so the `ow_tp` catalog is
  managed by a `terraform_data` resource that runs `CREATE CATALOG` /
  `DROP CATALOG ... CASCADE` through the SQL Statement Execution API on the
  reused warehouse. Everything else uses native provider resources. Changing
  `catalog_name` replaces this resource (drop old catalog, create new); the
  cascade also drops the provider-managed schemas/volume, so run `terraform
  apply` a second time after a rename to recreate them.
- **PAT without the `files` scope:** the demo token cannot use the Files API,
  so `make databricks-recon` stages inputs via the workspace import API under
  `/Shared/ow_tp/landing/`, and the ingest notebooks copy them into the
  `landing` UC volume. The staging directory lives inside the Terraform-managed
  `/Shared/ow_tp` directory (`delete_recursive = true`), so destroy removes it.

## Apply

```bash
export DATABRICKS_HOST="$DATABRICKS_DEMO_HOST"     # never hardcode
export DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"

cd infrastructure/terraform-databricks
terraform init
terraform apply
```

Apply takes under a minute. Outputs include the landing volume path, the
reused warehouse id, and both job ids (consumed by `make databricks-recon`).

## Destroy

```bash
cd infrastructure/terraform-databricks
terraform destroy
```

Destroy drops the catalog with `CASCADE` (removing all tables/volumes created
by pipeline runs), deletes both jobs, the secret scope, and the whole
`/Shared/ow_tp` workspace directory including staged recon inputs. Verify
cleanliness with:

```bash
databricks catalogs list | grep ow_tp   # or SHOW CATALOGS LIKE 'ow_tp*'
databricks jobs list | grep ow_tp
databricks secrets list-scopes | grep ow_tp
```

All three should return nothing after destroy.

## What maps to what

| Legacy | Terraform-managed replacement |
|---|---|
| cron + `run_all.sh` + `sleep 60` | `ow_tp_custbill_lakehouse` job (dependency-ordered tasks) |
| `sftp_ingest_poll.ksh` settle hack | `landing` UC volume (atomic PUT) + bronze ingest task |
| `parse_custbill_fixedwidth.sh` | silver parse task (validation + quarantine + trailer audit) |
| `finance_excel_report.pl` | gold finance task |
| 5 Python cron scripts (`etl/scripts/`) | `ow_tp_python_etl_wave` job, one task per script |
| hardcoded creds (`etl/config.ini`, plaintext `mvsprod`) | `ow_tp` secret scope |

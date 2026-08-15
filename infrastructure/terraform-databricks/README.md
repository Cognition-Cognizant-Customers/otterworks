# Databricks lakehouse estate (tech-partnerships migration target)

Terraform stack for the target state of the legacy batch estate: the Unity
Catalog layers the converted ETL jobs land in, the landing volume that replaces
the SFTP drop directory, the secret scope that replaces the credentials
hardcoded in `etl/config.ini` and `etl/legacy-extra/jobs/*`, and one job
definition per converted legacy script (`jobs_<unit>.tf`, added by each
conversion work unit).

## Shared-workspace rules

The demo workspace is shared with other demos, so:

- every object this stack creates carries the `ow_tp` prefix — catalog `ow_tp`,
  jobs `ow_tp_*`, secret scope `ow_tp`, workspace directory `/Shared/ow_tp`.
  The prefix is validated in `variables.tf` and in `catalog.sh`;
- nothing unprefixed is read-write; the only pre-existing object referenced is
  the serverless SQL warehouse, by name, as a data source;
- **no compute is created.** Jobs run on serverless job compute, SQL and recon
  queries on the existing warehouse. Nothing here has an hourly floor.

## Usage

```bash
export DATABRICKS_HOST="$DATABRICKS_DEMO_HOST" DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
make dbx-init
make dbx-apply                    # catalog + bronze/silver/gold + volume + scope + jobs
make dbx-inventory                # what the demo owns right now
make dbx-destroy                  # full teardown
make dbx-verify-teardown          # exits non-zero if anything ow_tp survived
```

Demo state is per run and per namespace (`NS=<ns>`): the landing volume is laid
out `<ns>/custbill/...` and jobs take an `ns` parameter, so repeated rehearsals
and parallel namespaces never collide. Branches hold code only.

## Catalog creation

`databricks_catalog` cannot create a catalog in this workspace: Default Storage
is enabled and the Unity Catalog REST API demands an explicit managed location,
while `CREATE CATALOG` over the SQL Statement Execution API works. The catalog
is therefore a `terraform_data` resource driving `catalog.sh`, whose destroy
provisioner runs `DROP CATALOG ... CASCADE` — that also removes tables created
by pipeline runs outside Terraform's view, which is what makes teardown
verifiable.

## Secrets

`var.secrets` populates the `ow_tp` scope and defaults to placeholders. Real
values are passed as `TF_VAR_secrets='{...}'` at apply time and never
committed; converted jobs read them through `dbutils.secrets`, never inline.

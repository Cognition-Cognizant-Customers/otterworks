# Cron Box Databricks bundle

This bundle owns only shared Cron Box workspace scaffolding. It targets the
existing serverless SQL warehouse through `sql_warehouse_id`; it does not
create clusters, job compute, notebooks, or per-unit tables.

The bootstrap SQL ensures catalog `ow_tp`, schemas `bronze`, `silver`, and
`gold`, plus the managed volume `/Volumes/ow_tp/bronze/landing`. The two
scheduled jobs are named for the Databricks units and remain `PAUSED`. Their
SQL files are placeholders for the child units.

Validate without deploying:

```sh
databricks bundle validate
```

The parent owns deployment. Do not run `databricks bundle deploy` from a child
session.

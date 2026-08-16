# Databricks fixture feasibility boundary

The converted units use Databricks/Spark SQL features (`read_files`, Delta
`MERGE`, `try_cast`, `from_json`, `regexp_extract`, window functions, and
`dbutils`/Volumes transport). DuckDB is not a dependency of this branch and a
local SQL compatibility layer would make a green fixture run misleading.

`scripts/tp_databricks/local_fixture.py` therefore implements only the viable
boundary: copy source artifacts into a namespace-isolated local `landing/`
directory, preserve bytes and checksums, and verify a rerun. It reports all
nine converted units as `transport-only`; SQL execution, Delta semantics,
Unity Catalog, permissions, and serverless warehouse behavior remain live
Databricks checks.

Run:

```sh
make tp-fixture-land NS=demo FIXTURE_SOURCE=etl/legacy-extra
make tp-fixture-verify NS=demo
make tp-fixture-clean NS=demo
```

## Capability preflight configuration

The preflight defaults target this estate but can be reused for another one:

| Variable | Default | Purpose |
|---|---|---|
| `TP_DATABRICKS_CATALOG` | `ow_tp` | Unity Catalog catalog |
| `TP_DATABRICKS_LANDING_PATH` | `/Volumes/ow_tp/bronze/landing` | Files API landing root |
| `TP_AWS_NAME_PREFIX` | `ow-tp-` | AWS leftover/name scan prefix |
| `TP_AWS_PROJECT_TAG_KEY` | `Project` | AWS ownership tag key |
| `TP_AWS_PROJECT_TAG_VALUE` | `otterworks-tp` | AWS ownership tag value |
| `TP_ATLAS_API_BASE` | Atlas v2 API URL | Atlas API base URL |
| `TP_ATLAS_TEST_IP` | `203.0.113.254` | Self-cleaning access-list probe IP |

Credentials remain platform-specific environment variables documented by the
runbooks.

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

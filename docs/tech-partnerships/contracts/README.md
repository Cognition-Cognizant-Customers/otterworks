# Databricks migration contracts (tech-partnerships track)

One contract per legacy work unit. A contract is the acceptance definition for the
converted Databricks job: the legacy source, the deficiencies the conversion must
retire, the target Unity Catalog tables, where the golden legacy output lives, and the
reconciliation checks that must pass to the cent.

## Shared rules (apply to every unit)

- Target estate is parent-owned and already applied: catalog `ow_tp`, schemas
  `bronze` / `silver` / `gold`, managed volume `/Volumes/ow_tp/bronze/landing`, secret
  scope `ow_tp`, notebooks under `/Shared/ow_tp`. See
  `infrastructure/terraform-databricks/README.md`.
- **Never** apply the shared stack (`main.tf`, `catalog.sh`, `variables.tf`,
  `versions.tf`, `outputs.tf`) and never `terraform apply`/`destroy` at all — the parent
  session owns workspace state. Contribute your job as a new file
  `infrastructure/terraform-databricks/jobs_<unit>.tf` only.
- Everything created carries the `ow_tp` prefix; jobs are named `ow_tp_<unit>`. This is a
  shared workspace — never read, write, or delete an unprefixed object, and never create
  a cluster or any resource with an hourly cost. All compute is the existing serverless
  SQL warehouse (data source `databricks_sql_warehouse`) or serverless notebook tasks.
- Demo state is per-run and per-namespace: every job takes an `ns` parameter (`demo` for
  this run), volume paths are `<ns>/<unit>/...`, and table rows carry `ns`. Branches hold
  code only — no data, no state files, no secrets.
- Use `scripts/tp_databricks/dbx.py` for SQL, volume uploads, notebook deploys, and job
  runs instead of hand-rolling REST calls.
- Conversions must retire the deficiencies listed in the unit's contract; they are drawn
  from `etl/ETL_UPGRADE_GUIDE.md` and
  `etl/legacy-extra/ETL_UPGRADE_GUIDE_ADDENDUM.md`.
- The legacy scripts under `etl/` are the demo's before-state: **do not edit them**, and
  do not touch the golden app path (`make up` / `make test`, `services/`, compose files,
  CI). Every PR must pass `make tp-smoke`.
- Deliver a stacked PR series, bottom-up mergeable, based off `tech-partnerships`:
  1. `jobs_<unit>.tf` (+ table DDL for your own tables),
  2. pipeline code (notebook / job source),
  3. recon evidence (recon script + committed report).
  Never one monolithic PR. Never merge to `main`; never merge from
  `tech-partnerships-solutions` (reference only).

## Reconciliation honesty rule

Recon compares the converted job's output against the **golden legacy output** captured
from an actual legacy run — not against itself, and not against numbers copied out of a
document. If the legacy baseline for your unit cannot be produced (a dependency the
legacy script needs does not exist locally), you must:

1. try to stand up the missing local fixture so the legacy script can run (see the
   unit's contract for what is missing and what has already been seeded), then
2. if it still cannot run, report `recon_result: blocked` with the exact command,
   the exact error, and what is missing.

Never synthesize a golden output, never soften a comparison to make it pass, and state
the provenance of your baseline explicitly in the recon report.

## Units

| Unit | Language / vintage | Contract |
|---|---|---|
| `sftp_ingest_poll.ksh` | ksh, 1998 (ported 2014) | [sftp_ingest_poll.md](sftp_ingest_poll.md) |
| `parse_custbill_fixedwidth.sh` | bash + sed/awk/cut, 2001 | [parse_custbill_fixedwidth.md](parse_custbill_fixedwidth.md) |
| `finance_excel_report.pl` | Perl (no modules), 2004 | [finance_excel_report.md](finance_excel_report.md) |
| `analytics_daily.py` | Python, 2014 | [analytics_daily.md](analytics_daily.md) |
| `audit_archive_weekly.py` | Python, 2014 | [audit_archive_weekly.md](audit_archive_weekly.md) |
| `search_reindex_weekly.py` | Python, 2014 | [search_reindex_weekly.md](search_reindex_weekly.md) |
| `storage_cleanup_daily.py` | Python, 2014 | [storage_cleanup_daily.md](storage_cleanup_daily.md) |
| `user_activity_daily.py` | Python, 2014 | [user_activity_daily.md](user_activity_daily.md) |
| `run_all.sh` + estate rollup | bash, 2014 | [gold_estate_rollup.md](gold_estate_rollup.md) (second wave) |

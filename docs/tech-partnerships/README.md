# Tech-Partnerships Demo Track — build contracts

This directory coordinates the work on the `tech-partnerships` branch: building
deliberately-legacy "before" states and seeded data so the MongoDB, Databricks, and AWS
partner demos (standalone or the combined "Modernize OtterWorks" demo) start from a
realistic, data-rich estate.

**Golden-app rule:** everything here is additive. `make infra-up && make up` and
`make test` must behave exactly as on `main`. New estates live behind their own compose
files and Make targets and are never started by the default path.

## Component contracts

| Component | Compose file | Make targets | Location |
|---|---|---|---|
| Oracle billing estate (PL/SQL port of legacy-billing + data-model horror schema) | `docker-compose.oracle-billing.yml` | `oracle-billing-up`, `oracle-billing-down` | `services/legacy-billing/db/oracle/` |
| Polyglot batch estate (Perl/ksh/SFTP jobs, overlapping cron, run_all.sh) | n/a (host-run scripts; optional SFTP container in the same compose file) | `legacy-etl-list`, `legacy-etl-run JOB=<name>` | `etl/legacy-extra/` |
| Seed-data generators (deterministic, per-namespace) | n/a | `seed-legacy NS=<ns> [SCALE=demo\|full]` | `testdata/legacy/` |

Conventions:

- **Oracle**: reuse the existing Oracle Database Free fixture pattern from
  `docker-compose.insurance.yml` (image, startup-script volume, `FIXTURE_META`
  completion-marker healthcheck, localhost-bound port). Default port `52521`, PDB
  `FREEPDB1`, schema owner `OW_BILLING`.
- **Namespacing**: seeders take `NS=<ns>` like the existing `testdata/` and `procs/`
  harnesses so tenants can be seeded concurrently.
- **Determinism**: all generators use a seeded RNG derived from `NS`, so a namespace
  reproduces byte-identical counts across runs and rehearsals.

## Seed manifest contract

Every seeding run writes a manifest to `testdata/legacy/manifests/<ns>.json` — this is
the "before" contract that migration reconciliation reports must match:

```json
{
  "namespace": "dev",
  "generator_version": "1",
  "seed": 1042,
  "generated_at": "2026-08-14T00:00:00Z",
  "targets": {
    "oracle.OW_BILLING.CUSTOMER_MASTER": {"rows": 250000, "checksum": "<md5 of ordered PK+amount columns>"},
    "oracle.OW_BILLING.INVOICE_LINE": {"rows": 5000000, "checksum": "..."},
    "postgres.otterworks_docs.documents": {"rows": 100000, "checksum": "..."},
    "dynamodb.file-metadata": {"items": 500000, "checksum": "..."},
    "s3.data-lake/events/": {"objects": 2160, "bytes": 0}
  },
  "planted_anomalies": [
    {"kind": "orphaned_rows", "target": "oracle.OW_BILLING.INVOICE_LINE", "count": 137},
    {"kind": "dirty_dates", "target": "oracle.OW_BILLING.CUSTOMER_MASTER.SIGNUP_DT", "count": 412}
  ]
}
```

- `planted_anomalies` are deliberate data-quality defects the generators inject (and can
  enumerate exactly) so migration recon has real findings to surface.
- `SCALE=demo` targets seeding in under ~15 minutes on a laptop; `SCALE=full` may be
  larger for cloud runs.

## CI

`ci.yml` is unchanged. A lightweight smoke job for the new estates (boot Oracle fixture,
run one legacy ETL job against seeded data) is added as a separate, non-blocking workflow
until the estates stabilize, then promoted to required.

### Golden-path smoke gate

`.github/workflows/tp-golden-smoke.yml` runs on every PR targeting `tech-partnerships`
(and only that branch — nothing on `main` changes). It proves the golden path still works
while the legacy estates evolve, in well under 10 minutes:

- **Fast core services**: api-gateway (`go vet` + tests + build), collab-service
  (lint + tests + build), search-service (pytest).
- **Estate entry points parse**: `make -n oracle-billing-up`, `make -n seed-legacy NS=ci`,
  `make -n legacy-etl-list`, `make -n procs-parity NS=ci`, plus
  `docker compose -f docker-compose.oracle-billing.yml config` (Oracle Database Free is
  never booted in CI — too heavy).
- **Golden contract**: `make -n test` still parses unchanged.

Run the same checks locally before opening a PR with `make tp-smoke`.

Deliberately out of scope (known pre-existing failures or too slow for a smoke gate,
must not fail it): document-service pytest (env issues), auth-service Gradle
(Maven-Central 429s), the Scala/Rust/Ruby/C#/frontend suites, and anything needing
cloud credentials.

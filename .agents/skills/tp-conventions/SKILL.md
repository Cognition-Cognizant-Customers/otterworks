---
name: tp-conventions
description: Conventions for all tech-partnerships (TP) demo work in this repo — branch topology, namespacing, additive-only rules, IaC/teardown requirements, smoke gate, and recon contract. Applies when building or running the MongoDB/Databricks/AWS partner migration demos.
---

# Tech-partnerships conventions

## Branch topology
- `tech-partnerships` (off `main`): the legacy **before-state only** — Oracle billing
  estate, polyglot batch estate (`etl/legacy-extra/`), seed generators
  (`testdata/legacy/`), parity harness, runbooks (`docs/tech-partnerships/`), smoke gate.
  Live-migration demo children branch off it and PR into it.
- `tech-partnerships-solutions`: the completed after-state (Terraform stacks under
  `infrastructure/terraform-atlas|terraform-databricks|terraform-tp-aws`,
  `migrations/mongodb/`, `services/serverless-ingest/`). Rehearsed answer key — reference
  it, never present from it, never merge it back into `tech-partnerships`.
- Never merge TP work into `main`. Skills live on `main`.

## Additive-only / golden-app rule
`make infra-up && make up` and `make test` must behave exactly as on `main`. New estates
and tooling live behind their own compose files and Make targets. Every PR into a TP
branch must pass the golden-path smoke gate (`.github/workflows/tp-golden-smoke.yml`);
run `make tp-smoke` locally before opening a PR.

## Namespacing & determinism
- Everything is namespaced by `NS=<ns>`; seeders use a seeded RNG derived from NS, so a
  namespace reproduces byte-identical counts across runs.
- The seed manifest `testdata/legacy/manifests/<ns>.json` is the recon contract:
  migration reconciliation must match its counts/checksums AND surface every entry in
  `planted_anomalies` (a recon that misses planted anomalies is vacuously green).

## Cloud resource rules
- **Atlas**: managed via Terraform (`mongodbatlas` provider); API key needs Project
  Owner; M0 = 512MB → `SCALE=demo` only.
- **Databricks**: SHARED workspace — prefix everything `ow_tp` (catalog/schemas
  `ow_tp_bronze/silver/gold`, jobs `ow_tp_*`, secret scope `ow_tp`); never touch
  unprefixed objects; use the existing serverless SQL warehouse, never create clusters.
- **AWS**: serverless/on-demand only (no EC2, NAT, RDS, LBs); tag
  `Project=otterworks-tp`, prefix `ow-tp-`; never create AWS resources from Kubernetes.
- All cloud objects Terraform-managed in self-contained stacks (local state, gitignored);
  `terraform destroy` must be verified to remove everything before a PR is final.

## PR conventions
- Branch off the TP branch you target, `devin/<ts>-<slug>` naming.
- Deliver multi-layer work as a stacked PR series, bottom-up mergeable
  (infra → code → recon/evidence), not one monolithic PR.

## Known environment issues
- Maven Central 429 rate limits can break `make test`'s Java leg with zero code changes —
  preexisting/environmental; the smoke gate deliberately excludes those suites.
- Oracle Free image pull is multi-GB (10–20 min first boot); reuse the container.

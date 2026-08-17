# Demo Runbook — MongoDB Atlas migration + the Devin remediation loop

**Story:** *MongoDB catches it, Devin fixes it, the PR proves it.*
**Duration:** ~35 minutes. Every beat is triggered by hand; **nothing is on a schedule.**

This runbook is the run-of-show for the staged run of 2026-08-17. It layers on top of
`runbook-mongodb.md` (which owns the legacy before-state tour) and adds the three
delivered waves: data migration, stored-procedure extraction onto MongoDB, and the
platform showcase whose reconciliation job calls Devin when it fails.

| | |
|---|---|
| Run branch | `tp-run/mongodb-20260817T040807Z` (cut from `tech-partnerships`; the legacy before-state — every unit PR merges **into** it, nothing merges to `main`) |
| Atlas cluster | `otterworks-demo` (M0, AWS) |
| Persistent showcase namespace | `demo` → database `ow_tp_demo` — left up, green, and browsable |
| Rehearsal namespace | `drift01` → `ow_tp_drift01` — used for the failure beat, then destroyed |
| Baseline manifest | `testdata/legacy/manifests/demo.json` (seed `714559852`), 15/15 validator checks |

Credentials come from Terraform (`infrastructure/terraform/tp-mongodb`, one workspace per
namespace) and are injected as `MONGO_URI` / `MONGO_DB`; no command in this runbook prints
a password (see `redacted_uri` in `scripts/tp_mongo/platform_common.py`).

## Beat order

| # | Beat | Command | Expected on screen |
|---|---|---|---|
| 0 | Before-state | `make seed-legacy-validate NS=demo` | 15/15 PASS |
| 1 | Capability preflight | `make tp-preflight PLATFORM=atlas` | cluster read, wire write, access-list create+delete, `collMod` DDL on an existing collection all VERIFIED; `alert-webhook-config` DENIED (accepted — the recon job calls Devin directly) |
| 2 | Wave 1 — migrations reconciled | per-unit recon table below | 18/18, 16/16, 20/20, 12/12 checks pass, live against Atlas |
| 3 | Validators enforced | `make tp-mongo-validators NS=demo` | every collection has a `$jsonSchema`; every probe insert attempted is rejected server-side (`code 121`) |
| 4 | Deterministic aggregation | `make tp-mongo-report NS=demo OUT=<path>` | one pipeline replaces a 4-way legacy join; `Decimal128` money; run twice → byte-identical |
| 5 | Hard cutover | `make up` then the app's document/billing flows | services read MongoDB, not Postgres/Oracle |
| 6 | Full recon GREEN | `make tp-mongo-recon-platform NS=demo RUN_MODE=live` | `GREEN: 87 checks passed`, exit 0, **no** webhook call (failure-only notification) |
| 7 | Stage real drift (rehearsal ns) | `make tp-mongo-stage-drift NS=drift01 MUTATION=round_invoice_money COUNT=40` | 40 invoice headers mutated in the target — no report or check edited |
| 8 | Recon RED → Devin | `make tp-mongo-recon-platform NS=drift01 RUN_MODE=live` | `platform.invoice_header_total_matches_lines: expected=0 actual=18` fails, webhook POST returns HTTP 200, a Devin session spawns |
| 9 | Devin remediates | *hands off the keyboard* | Devin diagnoses, repairs from the immutable source, re-runs recon GREEN (87 checks), opens an audit PR into the run branch |

## Wave 1 — data migration (live Atlas evidence, `NS=demo`)

| Unit | Command | Live result | PR |
|---|---|---|---|
| customers (Oracle 155-col + EAV) | `make tp-mongo-customers-recon NS=demo RUN_MODE=live` | 18/18 pass — 25,000 docs, EAV folded (8,333), CSV → arrays, `DD-MON-YY` → BSON dates | [#962](https://github.com/Cognition-Partner-Workshops/otterworks/pull/962) |
| invoices (headers + embedded lines) | `make tp-mongo-invoices-recon NS=demo RUN_MODE=live` | 16/16 pass — 18,750 docs, 149,963 embedded lines, `Decimal128` money | [#964](https://github.com/Cognition-Partner-Workshops/otterworks/pull/964) |
| documents (Postgres + versions/snapshots) | `make tp-mongo-documents-recon NS=demo RUN_MODE=live RERUN=1 OUT=<path>` | 20/20 pass — 2,000 docs, 13,876 embedded versions, 390 snapshots | [#963](https://github.com/Cognition-Partner-Workshops/otterworks/pull/963) |
| files (DynamoDB metadata) | `make tp-mongo-recon-files NS=demo RUN_MODE=live` | 12/12 pass — 10,000 docs, tenant field preserved | [#961](https://github.com/Cognition-Partner-Workshops/otterworks/pull/961) |

Each recon recomputes from the target *after* writing, re-runs its own migration as a real
idempotency proof (canonical extended-JSON fingerprint unchanged), and asserts **exact
anomaly-set equality** against the manifest: 37 orphan invoice lines, 50 dirty signup
dates, 31 malformed CSV lists, 10 document version gaps, 6 orphaned snapshots, 40 orphaned
DynamoDB metadata items — no more, no fewer.

Phase-0 and platform enablement PRs on the same branch:
[#956](https://github.com/Cognition-Partner-Workshops/otterworks/pull/956) (preflight,
namespace Terraform, guarded teardown, unit contracts),
[#958](https://github.com/Cognition-Partner-Workshops/otterworks/pull/958) (single-owner
Atlas access-list entry), [#975](https://github.com/Cognition-Partner-Workshops/otterworks/pull/975)
(namespace `dbAdmin` so `collMod` reruns work, and a preflight probe that actually proves it),
[#982](https://github.com/Cognition-Partner-Workshops/otterworks/pull/982) (Atlas-compatible
`listCollections` filter), [#990](https://github.com/Cognition-Partner-Workshops/otterworks/pull/990)
(never print a URI with its password).

## Wave 2 — stored procedures extracted onto MongoDB

Rating ([#986](https://github.com/Cognition-Partner-Workshops/otterworks/pull/986)) and
invoicing ([#985](https://github.com/Cognition-Partner-Workshops/otterworks/pull/985)) are
extracted out of the legacy Oracle packages into the billing service, reading and writing
MongoDB. Parity is proved against **recorded transcripts of the legacy procedures** — the
transcripts are never re-recorded and the legacy SQL is never edited.

```bash
make procs-rules-gate MODULE=rating      # Rules gate PASS: rating      (10 rules)
make procs-rules-gate MODULE=invoicing   # Rules gate PASS: invoicing   (8 rules)
make procs-parity                        # PASS=19 FAIL=0 SKIP=5 (dunning still pending → SKIP)
```

`procs/routes.yaml` is the source of truth for which modules are graded: `plans`, `rating`,
and `invoicing` are `extracted` (graded), `dunning` is `pending` (skipped by design).

## Wave 3 — platform showcase

[#987](https://github.com/Cognition-Partner-Workshops/otterworks/pull/987) plus review
fixes [#988](https://github.com/Cognition-Partner-Workshops/otterworks/pull/988) deliver
the three showcase pieces and the failure loop:

1. **Schema validation** — every collection carries a `$jsonSchema` validator; the showcase
   attempts a missing-required-field insert, a wrong-BSON-type insert, and an unmodeled-field
   insert per collection and shows the **server** rejecting every attempted probe (error code
   121), with zero failures. Two by-design exemptions show up in the evidence as unattempted
   probes rather than passes: `files` / `files_quarantine` permit unmodeled fields (attributed
   DynamoDB extras), so the unmodeled-field probe is skipped there, and `documents_quarantine`
   has no sample document to mutate, so only the missing-required-field probe runs.
   Evidence: `docs/tech-partnerships/evidence/mongodb-20260817/demo-validator-showcase.json`.
2. **Deterministic aggregation report** — a single pipeline over `invoices` (embedded lines)
   joined to `customers` replaces a 4-way legacy join across `CUSTOMER_MASTER`,
   `ENTITY_ATTR_VALUE`, `INVOICE_HEADER`, and `INVOICE_LINE`. Money is summed server-side as
   `Decimal128` and rendered as exact decimal strings — no float arithmetic. Pipeline digest
   `2611b2702946222e9e8b062b5d013d77`; two consecutive live runs were byte-identical
   (md5 `c43cc8ec5a1f863ae530b355d088bd48`). Evidence:
   `docs/tech-partnerships/evidence/mongodb-20260817/demo-aggregation-report.md`.
3. **Reconciliation job + drift tooling** — `scripts/tp_mongo/recon_platform.py` runs all
   four unit recons plus cross-unit checks (87 checks total) and POSTs the failure payload
   to the Devin recon-failure automation **only when it fails**.

## The failure beat, as it actually ran

Rehearsal namespace `drift01` was seeded, migrated, and reconciled green (87/87) first.

1. **Drift staged in the target, not in a report:** the `round_invoice_money` drift class
   rounded the money on 40 migrated invoice headers. Values stayed valid `Decimal128`, so
   the `$jsonSchema` validator has nothing to object to — this is exactly the class of
   damage only reconciliation can catch.
2. **MongoDB caught it:** recon came back RED with one named failure out of 87 —
   `platform.invoice_header_total_matches_lines: expected=0 actual=18` (18 headers whose
   `total_amt` no longer equalled the server-computed sum of their own embedded lines).
   Planted-anomaly sets were still exact, so the failure was unambiguously post-migration
   value drift. Report:
   `docs/tech-partnerships/evidence/mongodb-20260817/drift01-platform.red.recon.json`.
3. **Devin was called:** the job POSTed the failure payload to the automation webhook →
   HTTP 200 → automation `OtterWorks Mongo recon failure — auto-remediate (MongoDB Atlas)`
   spawned a session. No human touched the drift.
4. **Devin fixed it:** <https://partner-workshops.devinenterprise.com/sessions/8eb130cde4c34d1fb93c17d5c404f7f2>
   diagnosed post-migration value drift (not a migration defect), repaired the 18 headers by
   re-deriving them from the immutable Oracle source via the idempotent migration rerun —
   no document hand-edited, no baseline or recon check modified — and re-ran the full live
   recon **GREEN: 87 checks passed**.
5. **The PR proves it:** audit PR
   [#991](https://github.com/Cognition-Partner-Workshops/otterworks/pull/991) into the run
   branch, with the incident note and the schema-validated green report
   (`docs/tech-partnerships/incidents/2026-08-17-drift01-*`).

## Artifacts

All committed under `docs/tech-partnerships/`:

| Artifact | Path |
|---|---|
| `demo` full recon, GREEN 87/87 (live) | `evidence/mongodb-20260817/demo-platform.green.recon.json` |
| `drift01` full recon, RED (live) | `evidence/mongodb-20260817/drift01-platform.red.recon.json` |
| `demo` validator showcase | `evidence/mongodb-20260817/demo-validator-showcase.json` |
| `demo` aggregation report (deterministic) | `evidence/mongodb-20260817/demo-aggregation-report.md` |
| Incident note + post-remediation green report | `incidents/2026-08-17-drift01-invoice-header-totals.md`, `incidents/2026-08-17-drift01-mongo-platform.recon.green.json` |
| Per-unit fixture recon reports | `recon/mongo_{customers,invoices,documents,files}.recon.json` |
| Unit contracts | `contracts/mongo_{customers,invoices,documents,files}.json` |

Recon reports are schema-checked: `make tp-validate-recon FILE=<path>` → PASS. The
validator and aggregation beats write their machine-readable evidence with `JSON_OUT=<path>`.

The live runs above are driven with the namespace's Terraform credential; a one-liner that
resolves it and exports `MONGO_URI` / `MONGO_DB` before calling the target keeps the demo to
a single command per beat.

## Post-demo state and cleanup

Left standing (do **not** tear down):

- Namespace `demo` / `ow_tp_demo` — nine collections with validators, recon green 87/87,
  aggregation report reproducible, extracted services pointing at MongoDB.
- Atlas access-list entry and database user `ow-tp-demo`, owned by the `demo` Terraform
  workspace (every other namespace opts out of access-list management, so tearing one down
  can never revoke `demo`'s access).

Rehearsal namespace teardown and its negative verification:

```bash
make tp-atlas-teardown NS=drift01
# drops ow_tp_drift01, destroys the namespace Atlas user + scoped objects, then verifies:
#   negative verification: ow_tp_drift01 is absent
#   negative verification: no namespace-scoped Terraform objects remain
```

Verified after this run: `listDatabases` shows `ow_tp_demo` present and `ow_tp_drift01`
absent; Atlas project database users are `otterworks-app` and `ow-tp-demo` only. Local
legacy fixtures come down with `make oracle-billing-down` and
`make testdata-clean NS=demo`; the Mongo fixture with `make tp-mongo-fixture-down`.

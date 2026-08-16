# Demo Runbook — Modernize OtterWorks (Combined, 60–90 min)

**Story:** one company, three legacy anchors, one modernization wave. Assess
the whole estate, fan out parallel Devin child sessions across the MongoDB,
Databricks, and AWS tracks simultaneously, then roll up: every track proves
itself against the same deterministic seed manifests.

This runbook composes the three standalone tracks — read them first:
`runbook-mongodb.md`, `runbook-databricks.md`, `runbook-aws.md`.

## Branch topology and determinism

- `tech-partnerships` is the legacy **before**-state only. It is never a PR
  target for migration work. Each rehearsal or live run cuts a fresh working
  branch with `make tp-run-branch TRACK=<mongodb|databricks|aws>`
  (`tp-run/<track>-<timestamp>`); every unit PR targets that branch. The
  smoke gate runs on `tp-run/*` PRs too.
- `tech-partnerships-solutions` is a fallback recording of a prior completed
  run. Consult it only if a live run fails; never merge it into
  `tech-partnerships`, and never treat it as the correctness reference — the
  golden baselines and recon schema are.
- Legacy jobs and golden recording run under `scripts/tp-run-deterministic.sh`
  (pinned `TZ=UTC LC_ALL=C`; set `TP_FAKETIME='2026-01-15 00:00:00'` to
  freeze the clock via libfaketime) so parity claims are byte-stable across
  machines and reruns.
- Tool versions are pinned in `mise.toml` (`mise install`) so a rehearsal and
  demo day run identical terraform/CLI binaries.

## Pre-demo checklist (run the day before; first Oracle boot is slow)

Namespace for the demo: `demo` (deterministic — all counts below reproduce
exactly). Run in this order:

```bash
# 1. Core infra (Postgres, Redis, LocalStack, MeiliSearch)
make infra-up
# if the host's own postgres holds :5432: sudo systemctl stop postgresql postgresql@14-main

# 2. Oracle billing estate (first boot pulls Oracle Free: 10–20 min)
make oracle-billing-up

# 3. Seed the app-store estates (~30 s)
make seed-legacy NS=demo

# 4. Seed the Oracle estate (~2–4 min)
make oracle-billing-seed NS=demo

# 5. Legacy ETL estate (needs ksh: sudo apt-get install -y ksh)
export OTTERWORKS_LEGACY_ROOT=/tmp/otterworks-legacy-demo
make legacy-etl-gen-data NS=demo
make legacy-etl-run JOB=run_all          # pre-run once so outputs exist
make legacy-etl-gen-data NS=demo         # re-drop input for the live Phase 1 run

# 6. Verify everything against the manifest (must print 15/15 checks passed)
make seed-legacy-validate NS=demo

# 7. (cluster track only) one demo tenant + one control tenant
scripts/deploy-tenant.sh moddemo --ttl 24h
scripts/deploy-tenant.sh control01 --ttl 24h
```

### Expected numbers (NS=demo, SCALE=demo — verify before the demo)

Manifest: `testdata/legacy/manifests/demo.json`, seed `714559852`,
`generated_at 2026-08-01T00:00:00Z` (fixed anchor — reruns are
byte-identical).

| Estate | Object | Expected |
|---|---|---|
| Oracle | `CUSTOMER_MASTER` | 25,000 rows |
| Oracle | `INVOICE_HEADER` / `INVOICE_LINE` | 18,750 / 150,000 rows |
| Oracle | `ENTITY_ATTR_VALUE` / `TENANTS` | 8,333 / 60 rows (`name LIKE 'demo::%'`; raw count is 69 incl. 9 static baseline rows) |
| Postgres | `otterworks_demo.documents` / `document_versions` / `document_snapshots` | 2,000 / 13,876 / 390 rows |
| DynamoDB | `otterworks-file-metadata` (ns=demo) | 10,000 items |
| S3 | `events/demo/` hourly objects | 71 objects, 340,945 bytes |
| ETL | CUSTBILL drops / parsed `.psv` rows | 2 files × 50 records / 100 rows |
| ETL | finance report rows | 6 (e.g. `EUR,INVOICE,22,101554.41`; full table in `runbook-databricks.md`) |

| Planted anomalies (the recon findings) | Count |
|---|---|
| Oracle orphaned `INVOICE_LINE` rows | 37 |
| Oracle dirty `SIGNUP_DT` strings | 50 |
| Oracle malformed CSV lists | 31 |
| Postgres document version gaps | 10 |
| Postgres orphaned snapshots | 6 |
| DynamoDB orphaned metadata items | 40 |
| S3 missing event hours | 1 |

If any number differs, you are not on namespace `demo` or a store was
partially reseeded — rerun steps 3–6 (reseeds wipe and regenerate; it is
safe).

## Phase 1 — Assess (0:00–0:20)

One estate tour, drawing the "three anchors" picture:

1. **Data anchor** (8 min) — Oracle horror + EAV + the Postgres/DynamoDB
   sprawl. Beats 1a–1c of `runbook-mongodb.md`. Garnish files:
   `services/legacy-billing/db/oracle/ops/deploy_prod_FINAL_v2.sh.txt`,
   `services/legacy-billing/db/oracle/ops/OPERATIONS_HANDBOOK.doc.txt`.
2. **Batch anchor** (7 min) — crontab archaeology + live `run_all` execution.
   Beats 1a–1c of `runbook-databricks.md`. Garnish:
   `etl/legacy-extra/ops/RESTART_PROCEDURE.doc.txt`.
3. **Platform baseline** (5 min) — the EKS tenant fleet that already works,
   and the manifest contract that makes the whole wave auditable. Beat 1 of
   `runbook-aws.md`; open `testdata/legacy/manifests/demo.json`.

## Phase 2 — Parallel wave (0:20–0:60)

Kick off all three tracks as concurrent Devin child-session groups. The
fan-out is safe because every work unit has a disjoint contract slice:

| Track | Children | Contract slice |
|---|---|---|
| MongoDB | `mongo-customers`, `mongo-invoices`, `mongo-documents`, `mongo-files` | manifest targets `oracle.*` (customer/invoice), `postgres.*`, `dynamodb.*` |
| Databricks | `dbx-ingest`, `dbx-parse`, `dbx-finance`, `dbx-orchestrate` (+5 Python-script children) | `$OTTERWORKS_LEGACY_ROOT` outputs + deficiency checklist |
| AWS | `aws-oracle-logic`, `aws-batch-serverless`, `aws-chaos-drills` | parity transcripts, EventBridge targets, tenant namespaces |

While the wave "runs", do the live beats:

- **0:20–0:30** — MongoDB modeling + fan-out beats (`runbook-mongodb.md`
  Beats 2–3).
- **0:30–0:40** — Databricks bronze/silver/gold + fan-out beats
  (`runbook-databricks.md` Beats 2–3).
- **0:40–0:55** — AWS chaos→remediation live on tenant `moddemo`
  (`runbook-aws.md` Beat 4): inject `file-upload-fails`, watch `control01`
  stay green, remediate, reset.
- **0:55–0:60** — Oracle-exit + serverless framing (`runbook-aws.md`
  Beats 2–3) as "what the remaining children are doing".

## Phase 3 — Rollup (0:60–0:80)

Reconciliation across all tracks against one source of truth:

1. `make seed-legacy-validate NS=demo` — live PASS table (15/15) proving the
   before-state still matches its manifest.
2. Recon reports per track: counts + checksums vs
   `testdata/legacy/manifests/demo.json`; the finance-report parity table vs
   the six deterministic rows; the anomaly ledger — all 175 planted defects
   (37+50+31+10+6+40+1) found, enumerated, and quarantined.
3. The platform evidence: chaos drill remediated on `moddemo`, `control01`
   untouched throughout.

Close: deterministic seeds + manifest contracts turn a modernization program
into a checkable, parallelizable, re-runnable wave — same numbers every
rehearsal, every demo, every region.

## Q&A buffer (0:80–0:90)

Likely questions:

- *"How does this scale past demo size?"* — `SCALE=full`: 250,000 customers /
  2,000,000 invoice lines / 100,000 documents / 500,000 DynamoDB items /
  90 days of events. Same determinism, same manifest contract.
- *"Can tenants run this concurrently?"* — yes; namespaces are the isolation
  unit end to end (schemas `otterworks_<ns>`, DynamoDB `ns` attribute, S3
  `events/<ns>/` prefixes, `OTTERWORKS_LEGACY_ROOT` per run).
- *"What about the golden app?"* — untouched: everything here is additive and
  behind its own compose files/targets (`docs/tech-partnerships/README.md`).

## Teardown

```bash
scripts/teardown-tenant.sh moddemo && scripts/teardown-tenant.sh control01
make oracle-billing-down
make testdata-clean NS=demo
make legacy-sftp-down 2>/dev/null; rm -rf $OTTERWORKS_LEGACY_ROOT
```

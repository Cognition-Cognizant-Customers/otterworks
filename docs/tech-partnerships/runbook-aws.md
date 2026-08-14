# Demo Runbook — AWS: Platform Engineering & Oracle Exit

**Duration:** ~30 minutes standalone.
**Story:** OtterWorks already runs a multi-tenant EKS fleet on AWS
(11 services, 8 languages, shared NLB/RDS/S3/DynamoDB — see `AGENTS.md` and
`docs/MULTI-TENANT-DEMO-PLAN.md`), but drags two anchors: an Oracle billing
estate that should not exist, and batch jobs pinned to a pet EC2 box. The
demo: frame the Oracle exit, identify serverless refactor candidates, then
prove operational maturity with live chaos→remediation on the tenant fleet,
all orchestrated by parallel Devin sessions.

## Pre-demo setup

**Local track (no cloud needed):**

```bash
make infra-up                     # Postgres + LocalStack
make oracle-billing-up            # Oracle Free, localhost:52521 (first boot 10–20 min)
make oracle-billing-seed NS=demo  # deterministic: 25,000 customers / 150,000 lines
```

**Cluster track (live chaos beats):** a running `otterworks-dev` EKS cluster
with at least one demo tenant deployed:

```bash
scripts/deploy-tenant.sh awsdemo --ttl 8h
```

Per `docs/MULTI-TENANT-RUNBOOK.md`; allow ~10 min for the tenant to go green.
If no cluster is available, run the chaos beats as a narrated walkthrough of
`scripts/bug-catalog.yaml` + `scripts/inject-bug.sh` (they read well on
screen).

## Beat 1 — The estate on AWS today (0:00–0:06)

Show the platform that already works:

- `infrastructure/terraform/` — S3, DynamoDB, SQS/SNS, EKS, RDS provisioning.
- `docs/MULTI-TENANT-DEMO-PLAN.md` — namespace-per-tenant model: per-tenant
  Redis/MeiliSearch/RDS database, shared NLB + node group, `ResourceQuota` +
  `NetworkPolicy` guardrails, TTL reaper, idle scale-to-zero.
- `demo-platform/reaper/` — `reaper.sh` (full teardown of expired tenants),
  `idle-suspend.sh` (scale-to-zero after 1 h idle), `infra-sweep.sh`
  (orphaned-ELB/EBS/DNS backstop, `DRY_RUN=true` by default).

Then the two anchors:

1. **Oracle**: `sqlplus ow_billing/ow_billing@localhost:52521/FREEPDB1` —
   155-column `CUSTOMER_MASTER`, cursor-loop PL/SQL packages, `DBMS_SCHEDULER`
   nightly jobs, autonomous-transaction logging. Garnish:
   `services/legacy-billing/db/oracle/ops/deploy_prod_FINAL_v2.sh.txt` (the
   "deployment process") and
   `services/legacy-billing/db/oracle/ops/OPERATIONS_HANDBOOK.doc.txt` (the
   tribal knowledge).
2. **The ETL pet box**: `etl/legacy-extra/crontab` — overlapping cron on one
   EC2 instance, `/var/log/etl/` as the alerting system.

## Beat 2 — Oracle-exit framing (0:06–0:12)

The migration thesis, mapped to AWS targets:

| Oracle workload | Where it goes | Why |
|---|---|---|
| `CUSTOMER_MASTER` + EAV + invoices (OLTP) | Amazon Aurora PostgreSQL (schema already exists: `services/legacy-billing/db/procs/` is the Postgres twin) or DynamoDB/Atlas for the document-shaped slices | The PL/SQL↔Postgres entrypoint mapping table in `services/legacy-billing/db/oracle/README.md` is the conversion contract — 12 entrypoints, functionally equivalent, parity-harness-comparable |
| `pkg_rating` / `pkg_invoicing` / `pkg_dunning` business logic | Extracted to the billing service (pattern: `procs/harness/` parity transcripts + rule ledger) | Cursor loops → set-based/service logic with recorded parity |
| `DBMS_SCHEDULER` jobs (`JOB_NIGHTLY_DUNNING`, `JOB_PURGE_AUDIT_LOG`) | EventBridge Scheduler → Step Functions / Lambda | Ships the "batch layer" off the database |
| The seeded estate itself | Recon evidence: `testdata/legacy/manifests/demo.json` — 25,000 / 150,000 rows with checksums, 37 orphaned lines, 50 dirty dates, 31 malformed CSVs enumerated | Migration proof is a diff against the manifest, not a slide |

## Beat 3 — Serverless refactor candidates (0:12–0:17)

Walk the candidates, cheapest first:

| Candidate | Today | Serverless target |
|---|---|---|
| Legacy ETL chain (`etl/legacy-extra/`) | ksh/Perl on a pet EC2 box, cron | S3 Transfer Family (mainframe drop) → EventBridge → Lambda/Step Functions; or the Databricks track (`runbook-databricks.md`) |
| Python cron scripts (`etl/*.py`) | Same box, `config.ini` plaintext creds | Step Functions + Lambda, Secrets Manager, EventBridge schedules |
| `demo-platform/reaper/*` cron | Scheduled from the ops dashboard | EventBridge Scheduler + Lambda (already stateless shell logic) |
| Notification fan-out | notification-service polling SQS | SQS → Lambda event-source mapping |
| Audit archival (`audit_archive_weekly.py`) | DynamoDB scan on cron | DynamoDB TTL + Streams → Firehose → S3 Glacier |

Guardrail to narrate (it's in `AGENTS.md`): never create AWS resources from
Kubernetes — the June 2026 stranded-NLB incident is the cautionary tale, and
`infra-sweep.sh` is the backstop.

## Beat 4 — Chaos → remediation on the tenant fleet (0:17–0:26)

The operational-maturity proof. Everything is scoped to one tenant namespace
(`otterworks-awsdemo`) and never touches other tenants or `main`.

1. **Inject** (pick from `scripts/bug-catalog.yaml`):

   ```bash
   scripts/inject-bug.sh awsdemo file-upload-fails    # chaos Redis flag, no redeploy
   ```

   Menu: `file-upload-fails`, `search-suggest-500`, `document-slow`,
   `notification-schema` (chaos flags); `file-bad-bucket` (config);
   `code-variant` (image swap).

2. **Observe**: uploads in the tenant's web app return 5xx; show the failure
   in Grafana/Jaeger (`observability/`) like an on-call engineer would.

3. **Remediate with Devin**: hand the incident to a Devin session (Flow 3 —
   automatic detection → investigation → remediation). It traces the 5xx to
   the chaos flag / bad config, proposes the fix, and verifies recovery.

4. **Reset & repeat** with a second scenario if time allows:

   ```bash
   scripts/inject-bug.sh awsdemo reset
   scripts/inject-bug.sh awsdemo document-slow
   ```

Isolation guarantee to state out loud: a second tenant deployed alongside
(`scripts/deploy-tenant.sh control01`) stays green throughout — per-tenant
Redis means chaos flags cannot leak.

## Beat 5 — Orchestration fan-out (0:26–0:29)

The platform story is parallel by construction; show the fan-out plan:

| Child session | Scope | Isolation boundary |
|---|---|---|
| `aws-oracle-schema` | Oracle DDL → Aurora PostgreSQL | manifest targets `oracle.*` |
| `aws-oracle-logic` | PL/SQL packages → billing service | parity transcripts per entrypoint |
| `aws-batch-serverless` | `DBMS_SCHEDULER` + cron → EventBridge/Step Functions | one child per job |
| `aws-etl-lift` | ETL box decommission | one child per script (see Databricks runbook) |
| `aws-chaos-drills` (×N) | one child per tenant × scenario | tenant namespaces (`otterworks-<id>`) |

Tenant namespaces + deterministic per-namespace seeds are what make N
concurrent children safe: no shared mutable state, reconciliation contracts
per slice.

## Beat 6 — Wrap (0:29–0:30)

- Platform is already cloud-native; the demo removes the last two anchors
  with evidence (manifests, parity transcripts), not promises.
- Segue to the combined demo: `runbook-modernize-otterworks.md`.

## Cleanup

```bash
scripts/inject-bug.sh awsdemo reset      # clear any active scenario
scripts/teardown-tenant.sh awsdemo       # or let the TTL reaper do it
make oracle-billing-down
```

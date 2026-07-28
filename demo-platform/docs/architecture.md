# OtterWorks Demo Platform — Design (Multi-Tenant Ops Dashboard)

Status: DRAFT for review. Grounded in the **actual** deployed infrastructure discovered on
2026-07-16 (not the idealized platform template). Decisions needed from you are in §11.

---

## 1. Current state (what's actually deployed)

| Thing | Reality today |
|---|---|
| EKS cluster | **`otterworks-dev`** only — created by `otterworks/infrastructure/terraform`, **not** by `platform-engineering-shared-services` (that repo's CDK targets a `workshop-dev` cluster for a *different* app — .NET decomposition — and is not deployed). |
| Ingress | 1 shared `ingress-nginx` + 1 NLB. |
| cert-manager / external-dns / ArgoCD | **Not installed** on `otterworks-dev`. |
| Route53 | **No hosted zones.** (nip.io is a stopgap.) |
| RDS | 1 shared instance; **database-per-tenant** (`otterworks_<id>`). Good — already single-instance. |
| S3 | **Multiple** buckets: `otterworks-files-dev`, `-audit-archive-dev`, `-data-lake-dev`, `-analytics-dev-*`. Shared across tenants (Tier-A prefixing), **not** one bucket. |
| DynamoDB | **Multiple** tables: `otterworks-file-metadata-dev`, `-file-shares-dev`, `-file-versions-dev`, `-folders-dev`, `-notifications-dev`, `-audit-events-dev`. Shared across tenants, **not** one table. |
| Tenants | `otterworks` (golden) + ephemeral `otterworks-<id>` (per-tenant ns + DB + in-cluster Redis/Meili). Deployed by `otterworks/scripts/deploy-tenant.sh`. |
| Tenant→branch | Not tracked anywhere. `workshop-<id>` branches exist by convention only. |
| State/registry | None. No record of which tenants exist, who owns them, TTLs, or branch mapping. |

**Implication:** to "roll out," we add a **control plane** (dashboard + durable state + reaper)
and **DNS/TLS** to the *existing* `otterworks-dev` cluster. We are not adopting the template repo's cluster.

---

## 2. Target architecture: two planes

### Platform plane — shared, always-on, ONE of each (control plane + shared data plane)
- EKS `otterworks-dev` + **autoscaled** node group(s)
- `ingress-nginx` + 1 NLB (shared L7 entry)
- **cert-manager** (wildcard TLS) + **external-dns** (auto Route53 records)  ← *to install*
- Route53 hosted zone for **`otterworks.app`** + wildcard `*.demo.otterworks.app`  ← *to create*
- **1 RDS instance** (database-per-tenant) — already so
- Shared S3 buckets + DynamoDB tables (namespaced *inside* by tenant key/prefix) — see §8
- **Demo Ops Dashboard** (new) — platform namespace `otterworks-platform`
- **Control-plane state store** (new) — 1 DynamoDB table `otterworks-demo-control` (durable, tenant-independent)
- **Reaper** (new, replaces the basic TTL reaper) — schedule owned by the dashboard

### Multi-tenant plane — ephemeral, one slice per checkout
- Namespace `otterworks-<id>` (11 services + 2 frontends, replicas=1)
- Per-tenant in-cluster **Redis** + **MeiliSearch**
- Its **database** `otterworks_<id>` on the shared RDS
- Its **prefix/owner partition** in the shared S3 buckets + DynamoDB tables
- Ingress host `t-<id>.demo.otterworks.app` / `api-t-<id>.demo.otterworks.app` (wildcard TLS)
- Mapped to git branch `workshop-<id>` (recorded in the control-plane state store)

See the rendered diagram: `platform-vs-multitenant.png`.

---

## 3. Demo Ops Dashboard

A small **platform service** (not per-tenant) in namespace `otterworks-platform`, exposed at
`https://ops.otterworks.app` (its own host, wildcard TLS).

**Backend API** (stateless; all state in the control table):
- `GET  /api/tenants` — list all tenants + live resources (ns status, pod ready counts, DB present, URL, owner, branch, tier, created/expires, node/cost estimate). Live data = control table joined with a live `kubectl`/AWS read.
- `POST /api/tenants/checkout` — `{ id?, branch, owner, ttl, tier }` → reserve id (atomic), record mapping, trigger deploy.
- `POST /api/tenants/{id}/checkin` — tear down + free id.
- `POST /api/tenants/{id}/extend` — bump TTL.
- `GET/PUT /api/reaper/schedule` — view/set reaper cadence + policy.
- `GET  /api/audit` — checkout/checkin/teardown history.
- `POST /api/tenants/{id}/inject` / `/reset` — optional: drive the bug catalog.

**Auth = passcode, server-side enforced:**
- Passcode stored as a K8s Secret (never in code/URL). Login exchanges it for a short-lived signed session cookie (HttpOnly, Secure, SameSite=Strict).
- Server-side **constant-time compare**; **rate-limited** + lockout on brute force. Every mutating endpoint re-checks the session server-side (no client-trust). Audit-logged.
- (Future-proof: swap the single passcode for per-facilitator tokens/OIDC without changing the API.)

**Frontend:** simple table UI (tenants, status, URL, owner, branch, TTL, actions: checkout / check-in / extend), a reaper-schedule panel, and an audit view.

---

## 4. State management — independent of ephemeral infra

A single DynamoDB table **`otterworks-demo-control`** (on-demand billing, PITR on) is the source
of truth. It **survives** any tenant teardown, cluster node churn, or reaper run.

```
PK = TENANT#<id>
  attrs: id, status(reserved|deploying|active|draining|error|free),
         owner, branch, tier, url, api_url, db_name,
         created_at, expires_at, last_seen_at, notes
PK = LOCK#<id>       # short-TTL item for atomic checkout (conditional put)
PK = CONFIG#reaper   # schedule (cron), grace period, policy flags
PK = AUDIT#<ts>#<id> # append-only audit events
```

- **Atomic checkout** via `PutItem` with `ConditionExpression=attribute_not_exists` on `LOCK#<id>`
  → two facilitators can't grab the same id (fixes the concurrency race).
- Reconciliation loop compares **desired** (table) vs **actual** (cluster/AWS) and repairs drift.

---

## 5. Reaper v2 (schedule-driven, cleans EVERYTHING)

Replaces the current TTL reaper (which deletes namespaces but **leaves orphan DBs**).

- Runs on the schedule stored in `CONFIG#reaper` (editable from the dashboard).
- For each expired/checked-in tenant, performs **idempotent, retry-safe** teardown of **all** its resources:
  1. namespace `otterworks-<id>` (pods/Redis/Meili/ingress)
  2. RDS database `otterworks_<id>`
  3. S3 objects under the tenant prefix in every shared bucket
  4. DynamoDB items with the tenant partition key in every shared table
  5. IAM/IRSA trust entries (only legacy exact-match; keep the shared wildcard)
  6. Route53 records (external-dns handles ingress records automatically)
  7. control-table status → `free`; append audit event
- **Orphan sweeper:** independently lists live namespaces / DBs / S3 prefixes / Dynamo partitions
  and reaps anything **not** present in the control table (belt-and-suspenders for crashes).

---

## 6. DNS + TLS (the scale answer)

- Register **`otterworks.app`** (Route53) → hosted zone.
- **external-dns** watches Ingress objects and writes `t-<id>.demo.otterworks.app` records automatically — **zero per-tenant DNS work**.
- **cert-manager** issues a single wildcard cert `*.demo.otterworks.app` (Let's Encrypt **DNS-01** via Route53) → HTTPS for every tenant + the dashboard.
- `deploy-tenant.sh` defaults to `--host-suffix demo.otterworks.app` + TLS annotations.
- Manifests + one-shot enablement: [`dns-tls.md`](./dns-tls.md) (`demo-platform/k8s/dns-tls/` + `scripts/enable-dns-tls.sh`). This is the AWS-native replacement for the temporary nip.io hostnames.

---

## 7. Scaling to high-tens of tenants

- **Nodes/cost:** ~15 pods per tenant. **Karpenter** owns tenant capacity (Spot, consolidation on idle); the managed node group is only the system pool. Rely on **scale-to-zero** for idle tenants and `--profile core` for labs that do not need all 13 services.
- **VPC IP exhaustion (VPC-CNI):** every pod takes a subnet IP. High-tens × 15 pods can exhaust subnets / per-node ENI caps → hard failure. **Enable prefix delegation** and/or widen subnets *before* scaling.
- **RDS connections:** pools × services × tenants exhausts `max_connections` (measured: 16 idle backends per tenant, ~112 available). **PgBouncer** now fronts the instance — same tenant, 1 server connection — with a session-mode port for migrations.
- **ingress-nginx:** each tenant adds Ingress objects → nginx config reloads; fine for dozens, watch reload time in the high-tens.
- **Least-privilege DB:** give each tenant DB its own restricted user instead of the RDS master (rotate via the control plane).

---

## 8. "Single instance per AWS managed service" — clarification + scope

Your instinct (1 instance, namespace inside) is right **and already true for RDS**. But S3 and
DynamoDB today are **multiple resources per data domain** (files/versions/shares/folders/…),
each shared across tenants via prefix/owner. Two interpretations:

- **(A) Keep the current shared set, enforce in-resource namespacing** (recommended, low-risk):
  they're already shared across tenants; we just guarantee no new *per-tenant* buckets/tables and
  that every access is tenant-scoped by prefix/owner. Reaper GCs per-tenant prefixes/items.
- **(B) True collapse to exactly one bucket + one table:** requires rewriting all 11 services'
  resource references + IAM + data model. Large app refactor, separate workstream.

I recommend (A) now and (B) only if you specifically want the consolidation. **Need your call.**

---

## 9. Security & correctness

- Passcode: server-side, constant-time, rate-limited, session cookie (§3). No secret in argv/URL/logs.
- Dashboard runs with a **scoped IRSA role** (only: manage `otterworks-*` ns, the control table, tenant DBs, tenant S3 prefixes) — not cluster-admin, not the tenant wildcard.
- **Audit log** of every checkout/checkin/teardown/extend (who, when, what).
- **Concurrency locking** on checkout (conditional writes, §4).
- Control table **PITR/backups** so state survives mistakes.

---

## 10. Rollout plan (phased; child agents in parallel where marked ⑂)

1. **DNS/TLS foundation:** register `otterworks.app`, hosted zone, install external-dns + cert-manager, wildcard cert. Move any existing tenants onto real hostnames.
2. **Control table** (`otterworks-demo-control`) + Terraform/CDK for it.
3. ⑂ **Dashboard backend** (API + passcode auth + state store client).
4. ⑂ **Dashboard frontend** (table UI + reaper panel + audit).
5. **deploy/teardown refactor** to read/write the control table + emit audit + host-based ingress by default.
6. **Reaper v2** + orphan sweeper (schedule from control table).
7. **Autoscaling + PgBouncer** (done) **+ prefix delegation** (scale hardening).
8. Deploy dashboard to `otterworks-platform`, wire `ops.otterworks.app`, end-to-end test, PR(s).

---

## 11. Decisions I need from you

1. **Repo home** for the dashboard + control plane:
   - (a) **`otterworks`** — co-located with the live cluster infra + tenant scripts (fastest).
   - (b) **`platform-engineering-shared-services`** — matches your "platform standard" knowledge, but that repo is currently a template for a *different* app / non-existent cluster; more reconciliation.
   - (c) a **new generalized repo** (e.g. `demo-tenant-operations`) if you want this reusable across demo apps.
   My lean: (a) now for speed, or (c) if reuse matters. 
2. **Single-instance scope:** §8 option **(A)** (enforce shared + namespacing) or **(B)** (true collapse)?
3. **Domain:** confirm **`otterworks.app` ($19/yr)**, approve the spend, and give the **registrant contact** (name, org, email, phone, postal) for Route53/ICANN.
4. **Passcode:** shall I generate one (stored as a Secret) or do you have a specific value?
5. **Child agents:** OK to spin up child sessions for the dashboard backend/frontend in parallel (steps 3⑂/4⑂)?

## 12. Anything you're missing? (my adds)
- Wildcard **TLS**, not just DNS (done in §6).
- **VPC IP exhaustion** — the sneaky hard-failure at high-tens (§7).
- **Concurrency lock** on checkout + **audit log** (§4/§9).
- **Orphan sweeper** independent of the scheduled reaper (§5).
- **Least-privilege per-tenant DB users** + scoped dashboard IRSA (§7/§9).
- **Cost guardrails**: max concurrent tenants, auto-extend caps, idle auto-scale-to-zero, nightly full-sweep.
- **State backups (PITR)** so a bad reaper run can't lose the registry (§9).

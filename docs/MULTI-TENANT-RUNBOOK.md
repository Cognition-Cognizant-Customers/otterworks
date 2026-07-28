# OtterWorks Multi-Tenant Demo — Operator Runbook

Execution of `docs/MULTI-TENANT-DEMO-PLAN.md`. Stands up many **isolated,
ephemeral** copies of the golden app on the **shared** `otterworks-dev` EKS
cluster, one per attendee/demo run (`ATTENDEE_ID` → namespace
`otterworks-<ATTENDEE_ID>`).

> The golden app is `main`. Tenants are derived from it; variants/bugs are
> injected per tenant and **never** flow back into `main`
> (see `AGENTS.md`).

## Scripts

| Script | Purpose |
|---|---|
| `scripts/tenant-platform-baseline.sh` | **Run once.** Installs the SHARED ingress-nginx (one NLB) and the namespace TTL reaper CronJob. |
| `scripts/deploy-tenant.sh <ID> [--tier A\|B] [--image-tag TAG] [--ttl 8h] [--host-suffix DOMAIN]` | Deploy/redeploy one tenant. |
| `scripts/teardown-tenant.sh <ID> [--keep-db] [--keep-trust]` | Delete one tenant (namespace + per-tenant DB + IRSA trust). |
| `scripts/inject-bug.sh <ID> <list\|reset\|scenario>` | Inject/clear a per-tenant bug (chaos flag / config / image). |
| `scripts/tenant-scale.sh <ID> <up\|down>` | Scale a tenant's compute to zero (or back) between sessions. |
| `scripts/bug-catalog.yaml` | The demo-scenario → variant registry. |
| `scripts/lib/tenant-common.sh` | Shared library (naming, TF-output loading, per-service Helm wiring). |

## Prerequisites (per shell)

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=us-east-1
# aws sts get-caller-identity  -> expect the workshop account (<AWS_ACCOUNT_ID>) and a Devin-PartnerWorkshops-Internal IAM identity
export DB_PASSWORD='<shared RDS master password>'
# Stable across redeploys so issued JWTs / Rails sessions stay valid:
export JWT_SECRET='<hex>' SECRET_KEY_BASE='<hex>'
```

Env vars do not persist between separate shell commands in some runners —
re-export within each command or combine into one.

## First-time setup

```bash
./scripts/tenant-platform-baseline.sh          # shared ingress + reaper (once)
```

## Spin up two tenants

```bash
./scripts/deploy-tenant.sh a01 --ttl 8h
./scripts/deploy-tenant.sh a02 --ttl 8h
kubectl get ns -l app.kubernetes.io/managed-by=otterworks-tenant
```

Reach a tenant's API without DNS:

```bash
kubectl -n otterworks-a01 port-forward svc/api-gateway 8080:8080
curl -s localhost:8080/api/v1/... 
```

With wildcard DNS, pass `--host-suffix demo.example.com` → the tenant is served
at `t-a01.demo.example.com` (web) and `api-t-a01.demo.example.com` (gateway)
through the one shared ingress/NLB.

## Isolation model (what is shared vs. per-tenant)

| Concern | Per-tenant mechanism |
|---|---|
| Compute | namespace + `ResourceQuota` + `LimitRange` + `NetworkPolicy`, `replicas=1` |
| Chaos flags / sessions / collab | **per-tenant in-cluster Redis** (`redis.<ns>`) — chaos keys are un-prefixed, so a shared Redis would leak bug injection across tenants; a dedicated Redis fully isolates it |
| Search | **per-tenant in-cluster MeiliSearch** |
| Relational data | **per-tenant RDS database** `otterworks_<ID>` on the shared instance (auth-service Flyway + document-service `create_all` self-provision the schema on boot) |
| Object storage | shared `otterworks-files-dev` bucket (objects keyed by UUID); listing is driven by the per-tenant DB / DynamoDB, so no cross-tenant listing |
| DynamoDB / S3 access | shared per-service **IRSA roles**; `deploy-tenant.sh` extends each role's trust policy to the tenant namespace's service accounts (dev-reuse model; the Terraform `modules/irsa` change makes this the reproducible default) |

**Tier A (default, implemented):** shared physical stores, isolated logically as
above. Blast radius: the shared S3 bucket and DynamoDB dev tables are physically
shared (mitigated because listings come from the per-tenant DB and objects use
UUID keys). Redis, MeiliSearch and the relational DB are fully per-tenant.

**Tier B (data-isolated):** additionally provision per-tenant DynamoDB tables and
scoped IRSA. **Not enabled by default** because the shared file-service IAM
policy is pinned to the `*-dev` table ARNs; enabling Tier B requires broadening
that policy resource to `otterworks-*` (or minting per-tenant roles) — see
"Known limitations". The per-tenant RDS database already gives Tier-B-grade
isolation for all Postgres-backed services today.

## Bug injection (per tenant, never touches others)

```bash
./scripts/inject-bug.sh a01 list
./scripts/inject-bug.sh a01 file-upload-fails     # chaos flag in a01's Redis only
./scripts/inject-bug.sh a01 reset                 # clear a01's chaos flags
```

Mechanisms: `chaos` (Redis flag, instant, auto-expiring), `config` (helm upgrade
+ rollout restart), `image` (variant image tag for one service). Fixing a bug
mid-demo is the same lever scoped to the one namespace (seconds).

## Cost controls

- One shared EKS cluster + node group; `replicas=1` per tenant; `ResourceQuota`
  caps each tenant (4 CPU / 8Gi requests, 40 pods).
- One shared ingress/NLB for all tenants (no per-tenant ELB).
- **Scale-to-zero** idle tenants: `./scripts/tenant-scale.sh <ID> down`.
- **TTL reaper** CronJob (every 15m) deletes tenant namespaces whose
  `demo/expires-at-epoch` annotation is in the past (integer compare only, so the
  reaper image needs nothing more than `date +%s`).
- Tenants reuse the golden ECR image tags; only variants build new images.

## Teardown

```bash
./scripts/teardown-tenant.sh a01     # drops ns + per-tenant DB + IRSA trust subs
```

## Verified live (2026-07-13, cluster `otterworks-dev`)

Stood up `a01` + `a02` concurrently on the shared cluster and confirmed:

- **Separate namespaces**, each 12/13 pods Running (`admin-service` crash-loops by
  design). Every tenant Service is `ClusterIP` — **no per-tenant LoadBalancer**;
  the only ELBs are the one shared `ingress-nginx` NLB and the golden app's.
- **Shared ingress routing:** `curl -H "Host: api-t-a01..." $NLB/health` → 200 and
  the same for `a02`; web hosts `t-a01/t-a02` → 200; unknown host → 404 — all
  through the single NLB.
- **Relational isolation:** a user registered in `a01` (`201`, login `200`) does
  **not** exist in `a02` (login `400`); re-registering the same email in `a02`
  succeeds (`201`) — proving independent per-tenant databases.
- **Bug isolation:** injecting `search-suggest-500` into `a01` only → `a01`
  `/search/suggest` returns `500` while `a02` stays `200`; `reset` restores `a01`.
- **Cost controls:** `tenant-scale.sh a02 down` → 15/15 deployments at 0 replicas
  (0 running pods), `up` restores them; the reaper kept both live tenants and
  deleted a synthetic expired namespace.
- **Teardown/cleanup:** both tenants removed — namespaces gone, per-tenant DBs
  dropped, and tenant subjects removed from the shared IRSA role trust policies.

Note: the 2-node SPOT group is sized for the golden app; running two extra full
tenants required scaling the shared node group to 4 (`t3.large` SPOT). Size the
shared group for the expected number of concurrent tenants (or enable an
autoscaler) rather than per-tenant node groups.

## Known limitations / honest gaps

- **Tier B DynamoDB** is documented but not enabled by default (IAM policy is
  ARN-pinned to the dev tables — see above). Postgres isolation via per-tenant DB
  is fully implemented.
- **Shared SNS/SQS eventing is left unwired for tenants** (`T_WIRE_EVENTING=false`)
  to avoid competing-consumer cross-talk on the shared queue. notification/search
  event pipelines are therefore inert per tenant; the request/response paths work.
- **NetworkPolicy** is applied for correctness but is only *enforced* if the
  cluster CNI has network-policy enforcement enabled.
- **`admin-service` crash-loops by design** (planted Rails logger bug on the
  golden app) — it is intentionally left broken in every tenant.
- Path-based ingress (no `--host-suffix`) serves an SPA under a sub-path with a
  rewrite; host-based routing is cleaner when wildcard DNS is available.

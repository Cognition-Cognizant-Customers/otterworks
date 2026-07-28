# AGENTS.md — OtterWorks

Guidance for AI agents (and humans) working in this repo.

## Golden app policy

- **`main` is the golden app: the canonical, fully-working initial state for every demo.**
  Unless a demo explicitly needs a variant, all demos start from the golden app as-is.
- The golden app is intentionally **fully functional except for deliberately planted bugs**
  used by bug-hunt / remediation labs. Planted bugs are a feature of the golden app, not
  defects to fix.
  - Do **not** "fix" a planted bug to make the app pass — that erases the lab. If you're
    unsure whether something is planted or a genuine infra gap, ask before changing it.
  - Known planted bug: `services/admin-service/config/environments/production.rb`
    (`ActiveSupport::TaggedLogging.logger($stdout)` is invalid on Rails 7.1 → admin-service
    crash-loops on boot). Leave it in place on the golden app.
- Genuine infrastructure/wiring gaps (missing tables, unwired config/secrets, unreachable
  backing services) **should** be fixed so the golden app is otherwise green.

## Variants & multi-tenant demos

- A **variant** = the golden app plus demo-specific changes (extra planted bugs, feature
  flags, scaled resources). Variants are derived from `main`, never the other way around.
- For concurrent demos on shared infra, isolate per attendee/demo rather than mutating the
  golden baseline. See `docs/MULTI-TENANT-DEMO-PLAN.md` for the namespace-per-tenant model,
  cost controls, and how to inject bugs / do immediate redeploys without stepping on others.

## Tenant isolation (for crafting bespoke demo branches)

Each demo runs as an **ephemeral tenant** on the shared EKS cluster (`otterworks-dev`).
`scripts/deploy-tenant.sh <ATTENDEE_ID>` stamps the golden app into a dedicated namespace so
many attendees run "their own OtterWorks" side by side without touching each other or `main`.
Full operator detail lives in `docs/MULTI-TENANT-DEMO-PLAN.md` (design) and
`docs/MULTI-TENANT-RUNBOOK.md` (step-by-step); this section is the mental model you need
before building a bespoke variant.

### What a tenant gets (isolated) vs. what it shares

Deploying tenant `<ID>` (namespace `otterworks-<ID>`) creates, **per tenant**:

- All 11 backend microservices + both frontends (`web-app`, `admin-dashboard`) via Helm,
  `replicas=1`, each with a per-tenant `ConfigMap`/`Secret`.
- A dedicated in-cluster **Redis** and **MeiliSearch** (so chaos flags, sessions, collab
  state, and search indexes never leak across tenants).
- A dedicated **PostgreSQL database** `otterworks_<ID>` on the shared RDS instance (all
  SQL-backed service data is isolated at the database level).
- Guardrails: `ResourceQuota`, `LimitRange`, and a `NetworkPolicy` that denies cross-tenant
  pod-to-pod traffic; ingress rules on the shared controller; a TTL label + reaper for
  auto-cleanup.

**Shared across all tenants (out of scope for per-tenant isolation):**

- The EKS cluster + SPOT node group (shared compute), the ingress-nginx controller and its
  single NLB (shared entry point), cert-manager, and monitoring — these are platform
  concerns and must **not** be duplicated per tenant.
- The RDS **instance** (isolated only logically, via the per-tenant database), and the
  physical **S3 buckets** / **DynamoDB tables** (Tier-A logical prefixing/partitioning only —
  see below). IAM/IRSA service roles are shared across `otterworks-*` via a wildcard trust.
- SNS/SQS eventing is **disabled** for tenants to avoid cross-tenant queue consumers.

### Isolation tiers

- **Tier A (default)** — logical isolation using the app's existing config knobs: per-tenant
  RDS database, Redis/MeiliSearch instances, and S3 key / DynamoDB owner prefixes on the
  shared buckets/tables. Cheap and instant; good enough for almost every demo.
- **Tier B** — physical isolation (on-demand per-tenant RDS schema + DynamoDB tables +
  scoped IRSA). Implement per `deploy-tenant.sh --tier B` where feasible; otherwise the
  limitation is documented in the plan doc.

### Ingress / URL model

Frontends ride the **shared** ingress-nginx NLB (never one LoadBalancer per tenant). With a
DNS zone, prefer host-based routing: `deploy-tenant.sh <ID> --host-suffix <domain>` →
`t-<ID>.<domain>` (web) and `api-t-<ID>.<domain>` (api). Without wildcard DNS it falls back
to path routing on the shared NLB (`/<ID>/` and `/<ID>/api/...`). Note the Next.js SPA emits
absolute `/_next/...` asset paths, so a sub-path only fully renders in a browser when that
tenant is served at the ingress **root** (fine when it's the only tenant); multi-tenant
browser use wants host-based routing.

### Crafting a bespoke demo branch

The golden app is the base for every variant; **never mutate `main`** to build a demo. Two
ways to make a tenant behave differently:

1. **No code change (preferred, instant, per-tenant):** inject a scenario from
   `scripts/bug-catalog.yaml` with `scripts/inject-bug.sh <ID> <scenario>` (clear with
   `... <ID> reset`). Mechanisms, lightest first: a **chaos** Redis flag in the tenant's own
   Redis (no redeploy, auto-expires), a **config** override (`helm upgrade` one release +
   rollout restart), or an **image** swap. All are scoped to `otterworks-<ID>` and never
   affect other tenants or `main`.

2. **Code-level variant (bespoke branch):**
   - Branch off `main` — participants use `workshop-<attendee_id>`; never point them at
     internal `devin/...` branches. Plant the demo-specific change on that branch.
   - Build the affected service image and push it to ECR under a unique tag (the deploy
     script derives the registry from `$AWS_ACCOUNT_ID`).
   - Deploy the tenant pinned to that image: `deploy-tenant.sh <ID> --image-tag <tag>`, or
     override a single service with `BUG_IMAGE_TAG_<service_with_underscores>=<tag>`
     (e.g. `BUG_IMAGE_TAG_file_service`). Roll back by redeploying with the golden tag.

**Isolation guarantees to rely on:** a write in one tenant is invisible to another (separate
DB + Redis + MeiliSearch); injecting a bug in one tenant does not degrade others; and none of
the above changes `main`. Verify per the "Live verification" section of the runbook.

### Lifecycle / cleanup

- Tenants are TTL-labeled (`deploy-tenant.sh <ID> --ttl 8h`). The platform reaper
  (`demo-platform/reaper/reaper.sh`, scheduled from the ops dashboard) does the **full**
  teardown of an expired tenant: namespace, the `otterworks_<ID>` database, its S3 prefix
  and DynamoDB partitions, IRSA trust and DNS records. `scripts/teardown-tenant.sh <ID>`
  does the same thing on demand for a single tenant.
- Idle cost control: tenants that take no ingress traffic for an hour are scaled to zero
  automatically (`demo-platform/reaper/idle-suspend.sh`); the dashboard wakes them on
  check-out. Manually: `scripts/tenant-scale.sh <ID> down|up`.
- Deploy only what a lab needs: `deploy-tenant.sh <ID> --profile core` brings up the 5
  services a browser session exercises instead of all 13. See
  `demo-platform/docs/cost-and-scale.md` for the capacity and cost model.
- Pushing to `workshop-<id>` or `demo-<id>` ships that branch to its tenant automatically
  (`.github/workflows/cd-tenant.yml`), creating the tenant with a 72h TTL if it does not
  exist. The one exception is `t-main.otterworks.app`, the perpetual tenant tracking `main`:
  it is exempt from the reaper and idle-suspend and cannot be checked in or bug-injected.
  Never inject a scenario there — that is what a `workshop-<id>` tenant is for.

### Never create AWS resources from Kubernetes

A `Service` of `type: LoadBalancer` is provisioned by the in-cluster AWS cloud-controller,
not by Terraform. Nothing outside the cluster knows it exists, so deleting the Service while
the controller is down — or deleting the cluster at all — strands the load balancer, which
then bills indefinitely. Four were stranded this way in June 2026.

- The **only** permitted `LoadBalancer` Service is the shared ingress-nginx controller.
  Everything else is `ClusterIP` behind that one ingress; `deploy-dev.sh` fails the deploy
  if it finds otherwise.
- Tear the cluster down with `scripts/teardown-cluster.sh`, which drains load balancers and
  waits for AWS to release them **before** destroying the cluster.
- `demo-platform/reaper/infra-sweep.sh` is the backstop: it deletes load balancers, target
  groups, EBS volumes and DNS records whose owning cluster or Service no longer exists. It
  only touches resources carrying an ownership tag, and defaults to `DRY_RUN=true`.

## Deploy

- `scripts/deploy-dev.sh` wires all services' config/secrets from Terraform outputs and
  deploys via Helm. `scripts/spinup-dev.sh` / `scripts/teardown-dev.sh` manage cluster
  lifecycle for cost control. See `docs/SDLC-COVERAGE.md` §3 for the full CD picture.

# Scaling the demo platform to high-tens of tenants

Grounded in the live `otterworks-dev` cluster (one SPOT `t3.large` managed node group). Each
full tenant is ~15 pods (11 backends + 2 frontends + Redis + MeiliSearch). This doc lists the
concrete limits you hit as tenant count climbs and the fix for each — with ready-to-run
commands. Applied changes are noted; **opt-in** changes are scripted but intentionally NOT
force-applied to the live cluster (they recycle nodes and would disturb running tenants).

## 1. Node capacity / autoscaling — APPLIED (Karpenter)
- **Done:** **Karpenter** owns tenant capacity — `scripts/install-karpenter.sh` (controller,
  `EC2NodeClass`, `NodePool`) on top of `platform/terraform/modules/eks/karpenter.tf` (IRSA
  role, node instance profile, Spot interruption queue, discovery tags). The managed node
  group is now only the **system pool** — **one** node carrying Karpenter itself,
  ingress-nginx, cert-manager, external-dns, CoreDNS, PgBouncer and the dashboard — and no
  longer scales.
- **Why one and not zero, or two:** the platform has to be up to receive the checkout that
  creates a tenant, and Karpenter has to be running somewhere before it can launch anything,
  so zero would mean a multi-minute cold start on the first checkout of the day. Two bought AZ
  redundancy that a demo environment does not need and paid for it around the clock. At one,
  losing the node means ingress is down until the group replaces it rather than failing over;
  nothing that must survive it lives in the cluster (control table in DynamoDB, tenant data in
  RDS). Everything above the platform itself is Karpenter's, so this node does not grow with
  tenants.
- **Why not Cluster Autoscaler:** CA moves one fixed node group's `desiredSize`, so instance
  type is decided up front and a partly-used node is only removed when *entirely* empty.
  Karpenter picks the instance that fits the pending pods, and its
  `WhenEmptyOrUnderutilized` consolidation also *replaces* a large idle node with a smaller
  one — which is what makes cost track awake tenants rather than provisioned ones.
- **Measured:** pending pods → new Spot node **Ready in ~35s**; scaled back to zero → node
  drained and terminated ~2min later (`consolidateAfter: 2m`).
- **Limits:** `NodePool.limits` caps the pool at 400 vCPU / 1600Gi (~100 awake tenants at the
  measured ~4 vCPU each). Past that, pods stay Pending rather than the account being
  provisioned into the ground — raise it deliberately.
- **Spot interruptions:** EventBridge → SQS → Karpenter, so the 2-minute reclaim notice
  drains and replaces the node instead of tenants discovering it as a hard kill. Tenant pods
  are `replicas=1`, so a reclaim is a short restart, not zero downtime.
- **Teardown:** Karpenter instances exist in no Terraform state and are terminated only by
  the in-cluster controller. `scripts/teardown-cluster.sh` deletes NodeClaims before the
  cluster; `reaper/infra-sweep.sh` terminates any that outlive it.
- **Lean on scale-to-zero:** idle tenants cost nothing when scaled down
  (`demo-platform/reaper/idle-suspend.sh`, or `scripts/tenant-scale.sh <id> down`) — and with
  Karpenter the node they vacate now goes away too.

## 2. Pod IP exhaustion (VPC-CNI) — THE hard wall — OPT-IN
Every pod gets a real VPC IP. A `t3.large` allows **35** pods/node by default (3 ENIs × 12 − 1).
At ~15 pods/tenant that's ~2 tenants/node, and you exhaust private-subnet CIDRs fast.
- **Fix:** enable **VPC-CNI prefix delegation** (`ENABLE_PREFIX_DELEGATION=true`) → up to **110**
  pods/node and far denser IP packing (/28 prefixes instead of one IP per pod).
  `scripts/enable-prefix-delegation.sh` sets it; **new nodes** pick it up, so drain/recycle
  nodes during a quiet window. Also confirm the private subnets have enough free CIDR space
  (widen subnets / add a secondary CIDR if not).

## 3. Shared RDS connection limits — APPLIED (PgBouncer)
Every tenant database lives on one RDS instance, and each service holds its own pool, so raw
connections are `pool × SQL-services × tenants` whether or not anyone is using them. **Measured
on a live full tenant: 16 backends on `db.t3.micro`, idle.** Against that instance's ~112
connections, the platform runs out of database at **six tenants** — long before it runs out of
nodes or IPs.
- **Done:** one shared **PgBouncer** in `otterworks-platform` (`k8s/pgbouncer.yaml`,
  `scripts/install-pgbouncer.sh`); `deploy-dev.sh` and `deploy-tenant.sh` point every
  SQL-backed service at it. Wildcard routing (`* = host=<rds>`) passes the client's database
  name straight through, so `otterworks_<id>` still selects the tenant's own database and
  nothing is configured per tenant.
- **Measured:** the same idle tenant, rewired, holds **1** server connection instead of 16 —
  16 client connections multiplexed onto it. Redeploy with `DB_VIA_PGBOUNCER=false` to compare.
- **Ceiling:** `max_user_connections` (25 × 2 replicas transaction + 15 × 2 session = 80) is
  what bounds RDS, not the tenant count — server connections now track *concurrent queries*.
  Raise it when the instance is resized; it is sized to leave RDS's reserved superuser slots
  and room for `psql`/deploy jobs.
- **Two ports, on purpose:** `6432` is transaction pooling, `6433` the same pooler in session
  mode. Schema migrations take *session*-level advisory locks — Flyway on auth-service and
  analytics-service boot, `rails db:migrate` on admin-service boot — and a transaction pooler
  hands the next transaction a different server connection, so the lock is released on a
  connection that never held it (and Flyway's per-connection `search_path` goes with it).
  Migrations use `6433`. Only auth-service takes a separate migration URL
  (`SPRING_FLYWAY_URL`); admin-service and analytics-service migrate from the same URL their
  queries use, so those two sit on `6433` in full. **Adding a SQL service, or a migration
  runner to an existing one, means deciding which port it belongs on** — anything holding
  session state (advisory locks, session `SET`, `LISTEN`/`NOTIFY`, cursors across
  transactions) needs `6433`. analytics-service is the cautionary case: a failed migration
  there falls back to an in-memory store rather than crashing, so it fails silently.
- **Not through the pooler:** `CREATE`/`DROP DATABASE` cannot run inside a transaction, so
  tenant provisioning and teardown keep talking to the RDS endpoint directly.
- **Fallback:** if the pooler is not installed the deploy scripts wire straight to RDS and warn
  — a tenant pointed at a Service that does not exist would fail every query, which is worse
  than using more connections than intended.
- Per-tenant restricted DB users remain future hardening (see `plan-B-consolidation.md`); today
  every service authenticates as the one master user, so the per-user cap is a global cap.

## 4. Ingress / DNS — see architecture.md §6
One ingress-nginx + one NLB handles many Ingress objects fine. Move tenants to **host-based**
wildcard routing (`*.demo.otterworks.app`) so each tenant is its own origin (no cookie/asset
collisions, no nip.io fragility). external-dns + a single wildcard TLS cert cover all tenants.

## 5. Cost + concurrency guardrails
- Enforce a **max concurrent tenants** in the dashboard checkout path (reject over the cap).
- Default a short **TTL** (8h) and rely on the reaper; expose extend in the dashboard.
- Per-tenant `ResourceQuota`/`LimitRange` already cap spend (4 CPU / 8Gi req, 40 pods).
- Track a rough cost estimate per tenant in the dashboard (nodes × on-demand-equivalent).

## 6. Control-plane durability
The DynamoDB control table has **PITR** + deletion protection, so platform state survives
tenant churn, node recycles, and cluster loss — independent of the ephemeral infra.

## Quick capacity math
| Setting | pods/node | ~tenants/node | at 20 nodes |
|---|---|---|---|
| default `t3.large` | 35 | ~2 | ~40 (IP-bound first) |
| + prefix delegation | 110 | ~7 | ~140 (node/cost-bound) |

Autoscaling and PgBouncer are applied, so the remaining wall is pod IPs: high-tens (≈40–80
tenants) needs prefix delegation, without which you exhaust pod IPs well before node cost
becomes the limit.

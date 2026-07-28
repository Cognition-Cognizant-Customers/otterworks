# Cost and scale: 100 concurrent tenants

How the demo platform is sized and what each design decision is worth. The
starting point was a steady-state spend of roughly **$44/day (~$1,320/month)**
for a platform that was serving at most two tenants, which does not survive
contact with 100.

## 1. Where the money was going

Measured with Cost Explorer over a stable week (the 07-23 spike was unrelated
Mac dedicated hosts, since released):

| $/month | Line item | Verdict |
|--------:|-----------|---------|
| 432 | EKS control plane — **$360 of it an extended-support penalty** for running Kubernetes 1.32 past its standard-support window ($0.60/hr vs $0.10/hr) | pure waste |
| 346 | OpenSearch Serverless collection, idle — Serverless bills a **1 indexing + 1 search OCU floor 24/7** and never scales to zero | pure waste (unused) |
| 140 | 6 Classic ELBs + 2 NLBs | 4 were orphans |
| 97 | 3 RDS instances + 2 RDS proxies | 2 instances unrelated to the demo |
| 95 | 4 × `t3.large` spot nodes | legitimate, but over-provisioned |
| 86 | 17 public IPv4 addresses + 3 interface VPC endpoints | mostly avoidable |
| 65 | Lambda provisioned concurrency = 3, never invoked | pure waste |
| 52 | CloudWatch, EBS, ElastiCache, KMS, Secrets Manager | mostly legitimate |

About **$865/month was buying nothing at all.**

## 2. Why load balancers were orphaned

Four load balancers (3 Classic ELB + 1 NLB) created 2026-06-23 were still
running and billing on 2026-07-27 with zero backends.

The mechanism matters, because it will recur under any design that does not
address it:

1. A `Service` of `type: LoadBalancer` does not create an AWS resource in
   Terraform. The **AWS cloud-controller inside the cluster** creates it.
2. The only thing that deletes that load balancer is the same controller,
   reacting to the Service being deleted.
3. Delete the cluster — or delete the Service while the controller is already
   gone — and nothing is left that knows the load balancer should die. It has no
   Terraform state, no owner, and no TTL. It bills forever.

The cluster was replaced on 2026-07-09; the previous incarnation's load
balancers were stranded exactly this way. They still carried
`kubernetes.io/cluster/otterworks-dev = owned` tags pointing at a cluster that
no longer existed, which is precisely the signal the new sweep keys on.

The golden app made this worse by exposing `web-app`, `api-gateway` and
`admin-dashboard` as three separate `LoadBalancer` Services — three Classic ELBs
(~$54/month) doing a job the shared ingress controller already does, and three
more chances to strand a resource on every teardown.

### The fixes

| Fix | Where |
|---|---|
| No `LoadBalancer` Services anywhere except the one shared ingress controller | `infrastructure/helm/{web-app,api-gateway}/values.yaml`, `scripts/deploy-dev.sh` |
| Deploy fails if a `LoadBalancer` Service appears in the app namespace | `assert_no_loadbalancer_services` in `scripts/deploy-dev.sh` |
| Cluster teardown drains load balancers **first** and waits for AWS to release them | `scripts/teardown-cluster.sh` |
| Reaper sweeps AWS resources whose owning cluster or Service no longer exists | `demo-platform/reaper/infra-sweep.sh` |

The sweep is the backstop for the case the other three cannot cover: someone
deletes a cluster by hand, or the controller dies mid-teardown. It only deletes
a resource that (a) carries the ownership tag of a cluster listed as sweepable
and (b) whose owner is provably gone — untagged resources, and resources owned
by a cluster this platform never ran, are reported and never deleted, because
this account holds unrelated workloads.

Ownership is enforced twice, because a sweep that deletes the wrong thing cannot
be undone. The script checks the tag; the reaper's IAM role is then *only*
granted the deletes for those same cluster tags, so a bug or a misconfiguration
in the script is refused by AWS rather than acted on. That includes Classic
ELB: its API is widely documented as having no resource-level permissions, but
that is out of date, and the condition was verified against the live API.
Both `reaper.sweepableClusters` (Helm) and `var.sweepable_clusters` (Terraform)
hold only the *extra*, previously-run cluster names — the cluster the platform
runs on now is added by each side automatically and must not be listed. Keep
the two in step; a name in one but not the other means the sweep either cannot
clean those orphans or reports them and is denied.

Deletion is off by default even so: `sweep_infra` runs the sweep report-only,
and `sweep_infra_delete` arms it. Read a report before arming it.

## 3. What 100 tenants actually costs

A full tenant today is 15 application pods plus its own Redis and MeiliSearch:

| | CPU requests | Memory requests |
|---|---:|---:|
| 4 JVM services @ 512Mi | 0.4 | 2.0 GiB |
| 7 other backends @ 128Mi | 0.7 | 0.9 GiB |
| 2 frontends @ 128Mi | 0.2 | 0.3 GiB |
| Redis + MeiliSearch | 0.15 | 0.3 GiB |
| **per tenant** | **~1.5 vCPU** | **~3.5 GiB** |

Naively multiplying by 100:

> 150 vCPU / 350 GiB → ~45 × `m6a.2xlarge` spot → **~$3,600/month** in compute
> alone, before it fails on VPC IP exhaustion and the RDS connection ceiling.

That is the wrong number to design against, because it assumes 100 tenants are
all executing work simultaneously. They are not — workshop tenants spend most of
their life provisioned but idle (attendee reading, in a talk, gone home, or the
tenant is checked out for tomorrow). The design target is therefore:

> **cost scales with *active* tenants, not *provisioned* tenants.**

### The levers, in order of value

**1. Idle tenants run at zero replicas (≈10× reduction).**
A tenant scaled to zero costs nothing but its database rows and DNS record. With
100 provisioned tenants and a realistic 10–15 active at once, steady state is
~20 vCPU / 50 GiB — about **6 nodes, ~$400/month**. This is the single decision
that makes 100 tenants affordable, and everything else is a rounding error
beside it. `scripts/tenant-scale.sh <id> down|up` already does the scaling; the
reaper now drives it automatically from a last-activity timestamp, and the
dashboard wakes a tenant on check-out.

**2. Slim service profiles (≈2.5× on active tenants).**
Almost no lab needs all 13 services. `--profile core` deploys the 5 that a
browser session actually exercises (api-gateway, auth, file, document, web-app),
taking an active tenant from ~1.5 vCPU to ~0.5 vCPU. `full` stays the default —
`core` omits `admin-service`, whose planted crash-loop is the subject of the
bug-hunt labs — so this saving is opt-in per lab rather than automatic.

**3. Karpenter with consolidation instead of a fixed node group.**
A fixed `minSize` node group pays for capacity whether tenants are awake or not,
and its `maxSize` of 4 was already a hard ceiling at two tenants. Karpenter
provisions nodes when pods are pending and, critically, *consolidates* — as
tenants sleep it repacks the survivors and terminates the empty nodes. Combined
with spot and a wide instance-family list, compute tracks demand.

**4. Stop paying the Kubernetes version penalty ($360/month).**
Extended support costs 6× the standard control-plane rate and buys nothing. The
cluster is pinned to a supported version, and staying current is now an
operational requirement rather than a nice-to-have.

**5. Delete idle managed services ($411/month).**
OpenSearch Serverless has an OCU floor it will never drop below, so an unused
collection bills ~$346/month indefinitely; Lambda provisioned concurrency bills
whether or not anything invokes it. Neither was in use. Managed services that
cannot scale to zero do not belong in a demo platform unless a lab needs them.

### Resulting steady state

| | Monthly |
|---:|---|
| EKS control plane (supported version) | $72 |
| Nodes — ~6 spot, tracking active tenants | ~$400 |
| Shared RDS (`db.t4g.small`) + storage | ~$35 |
| NAT gateway, one shared NLB, Route53, DynamoDB control table, Secrets Manager | ~$60 |
| **baseline** | **~$570/month** |

Against ~$1,320/month for two tenants, that is 100 tenants for well under half
the previous spend.

### The floor, with every tenant asleep

What remains when no tenant is awake is the platform itself, and it is worth
knowing precisely, because it is the number that bills on a weekend:

| | Monthly |
|---:|---|
| EKS control plane | $72 |
| System pool — **one** spot xlarge | ~$36 |
| Shared RDS `db.t3.micro` + 20GiB | ~$15 |
| One shared NLB | ~$18 |
| Route53 zone + records, DynamoDB control table, Secrets Manager, EBS root, IPv4 | ~$10 |
| **idle floor** | **~$150/month** |

The control plane, one node and one NLB are ~85% of that, and each is load
bearing: the platform has to be reachable to receive a checkout, and Karpenter
has to be running somewhere before it can launch anything. Going below it means
giving up the warm start — tearing the cluster down between workshops leaves
only RDS and the hosted zone (~$15/month) but costs several minutes on the first
checkout of the day. That trade was considered and declined; see
`scaling.md` §1 for why the system pool is one node rather than zero or two.

### What the perpetual tenant adds

`t-main` is exempt from both TTL reaping and idle-suspend, so it is the one
tenant that is never asleep: ~15 pods holding roughly half a spot node,
**~$15-25/month** on top of the floor. That is the whole reason the flag is
allowlisted to a single id rather than offered as a checkout option — the idle
floor is only ~$150/month because nothing else stays up, and ten perpetual
tenants would more than double it while looking like a checkbox.

## 4. Scale limits to respect

These are the failure modes that appear between 10 and 100 tenants. They are not
cost problems; they are hard walls.

**VPC IP exhaustion.** The AWS VPC CNI assigns every pod a real subnet IP, and
each instance type has a fixed ENI/IP budget. At 15 pods per tenant this
exhausts both the per-node limit and the subnet CIDR long before compute runs
out. **Prefix delegation** (`ENABLE_PREFIX_DELEGATION=true`) makes the CNI
allocate /28 prefixes instead of single IPs, raising per-node pod density by
roughly an order of magnitude. Subnets are sized /20 to match.

**RDS connections.** Connections are `pools × services × tenants`, and they are
held whether or not the tenant is being used: a live full tenant sitting idle
measured **16 backends**, so a `db.t3.micro`'s ~112 `max_connections` runs out at
six tenants. **PgBouncer** in transaction-pooling mode now fronts the shared
instance, and the same idle tenant holds **one** server connection — RDS
connections track concurrent queries rather than tenant count, capped globally at
80 by `max_user_connections`. Migrations run through a second, session-mode port
(6433) because their advisory locks do not survive transaction pooling; see
[`scaling.md`](./scaling.md) §3.

**Database-per-tenant.** Postgres handles hundreds of databases on one instance
without complaint, so the isolation model itself scales; the constraint is
connections, not databases.

**IAM trust-policy churn.** Editing shared role trust policies on every
deploy/teardown races and throttles under concurrent tenant operations. The
`otterworks-*` wildcard trust rule already covers every tenant namespace, so
per-tenant trust edits are unnecessary and are skipped.

**Control-plane state.** Tenant state lives in a DynamoDB table that is
independent of the cluster, so a cluster rebuild does not lose the tenant
inventory — and, just as important, the reaper can still identify what *should*
exist in order to spot what should not.

## 5. What is shared and what is per tenant

| Layer | Shared (platform plane) | Per tenant |
|---|---|---|
| Compute | EKS cluster, Karpenter node pools | namespace `otterworks-<id>`, `ResourceQuota`, `LimitRange`, `NetworkPolicy` |
| Ingress | ingress-nginx + **one** NLB, wildcard TLS cert, external-dns | `Ingress` for `t-<id>` / `api-t-<id>` |
| Database | one RDS instance, PgBouncer | database `otterworks_<id>` |
| Object/NoSQL | S3 buckets, DynamoDB tables | key prefix `tenants/<id>/`, hash-key partition `<id>#…` |
| Search/cache | — | in-cluster Redis + MeiliSearch (asleep when the tenant is) |
| Control plane | DynamoDB control table, ops dashboard, reaper | `TENANT#<id>` item, branch mapping, TTL |

The rule that keeps this honest: **a tenant may never cause an AWS resource to
be created.** Everything a tenant needs either already exists (shared) or lives
inside Kubernetes and dies with the namespace. That is what makes teardown
total, and orphans impossible by construction rather than by cleanup.

## 6. Guardrails

- Every Terraform-managed resource carries `otterworks:managed-by`,
  `otterworks:component` and `Environment` via provider `default_tags`, so
  anything untagged in this account is by definition not ours.
- The reaper's infrastructure sweep defaults to `DRY_RUN=true` and is armed
  separately from the tenant sweep, in two stages: `CONFIG#reaper.sweep_infra` runs it
  report-only, and `CONFIG#reaper.sweep_infra_delete` arms it. Look at a report-only run
  before arming, since the account also holds resources this platform did not create.
- An AWS Budgets alarm on the account catches a regression in days rather than
  at the end of the month.

# OtterWorks Demo Platform (control plane)

A self-contained control plane for running **many ephemeral OtterWorks demo tenants** on the
shared `otterworks-dev` EKS cluster, designed to scale to **high tens of tenants**. It adds a
platform plane (dashboard + durable state + reaper + DNS/TLS) on top of the existing per-tenant
tooling in `../scripts/` (`deploy-tenant.sh`, `teardown-tenant.sh`, `inject-bug.sh`, …).

> Scoped to OtterWorks only (per decision), kept in this monorepo rather than
> `platform-engineering-shared-services`.

## Two planes
See `docs/architecture.md` and the diagram `docs/platform-vs-multitenant.png`.

- **Platform plane (shared, always-on, one of each):** ingress-nginx + 1 NLB, cert-manager
  (wildcard TLS), external-dns (Route53), 1 RDS instance, shared S3/DynamoDB (tenant-prefixed),
  the **Demo Ops Dashboard**, a durable **DynamoDB control table**, and the **reaper**.
- **Multi-tenant plane (ephemeral, per checkout):** namespace `otterworks-<id>` (all services +
  in-cluster Redis/Meili), its DB `otterworks_<id>`, its prefix in the shared S3/Dynamo, an
  ingress host `t-<id>.demo.otterworks.app`, mapped to git branch `workshop-<id>`.

## Layout
```
demo-platform/
  docs/          architecture, control-table schema, API contract, plan-B, diagram
  infra/terraform/  control table + dashboard IRSA (+ gated Route53/DNS IAM)
  dashboard/     Next.js ops dashboard (passcode auth, checkout/check-in, reaper panel)
  runner/        image that runs deploy/teardown/inject Jobs (carries repo + toolchain)
  reaper/        reaper v2 CronJob + orphan sweeper (schedule from control table)
  helm/          charts to deploy the dashboard + reaper into otterworks-platform
  scripts/       platform installers (Karpenter, PgBouncer) + tenant.sh (dashboard CLI)
```

## Checkout / check-in model
A **checkout** reserves a tenant id (atomic lock in the control table), maps it to an
OtterWorks git **branch** (`workshop-<id>`), and deploys that branch into `otterworks-<id>`.
A **check-in** tears the tenant down and frees the id. All state lives in the control table, so
it is **independent of the ephemeral infra** — it survives teardown, node churn, and pod
restarts. The reaper reconciles desired (table) vs actual (cluster/AWS) and GCs everything,
including **orphans** with no matching tenant record.

## Two kinds of environment

**One perpetual tenant, `main`** — `https://t-main.otterworks.app`, tracking the `main` branch.
It carries `persistent: true` in the control table, which makes it the one tenant the reaper
skips, the idle scan skips, and the dashboard refuses to check in or inject bugs into. It is
the reference environment: whatever is on `main` is what it shows, planted bugs included. It
bills continuously (~$15-25/mo), so the dashboard only accepts the flag for the ids in
`PERPETUAL_TENANT_IDS` — no one can make a hundred of these by accident. Create it once:

```bash
tenant.sh checkout main main never
```

**Everything else is ephemeral** — TTL'd, idle-suspended, and reaped. That is the default and
there is no way to opt an arbitrary id out of it.

## Continuous delivery
A push to `workshop-<id>` or `demo-<id>` builds the services that changed and ships them to
that branch's tenant (`.github/workflows/cd-tenant.yml`). If the branch has no tenant, CD
creates one with a **72h TTL**; if it has one, CD redeploys in place and keeps the TTL it had,
so shipping to an environment never extends its life. `workshop-derek` and `demo-derek` both
map to tenant `derek`, and the dashboard rejects a redeploy from a branch other than the one
the tenant was checked out from, so the two cannot quietly overwrite each other.

CD holds no cluster credentials. The workflow assumes an OIDC role
(`infra/terraform/iam_github_actions.tf`) trusted only for `main`, `workshop-*` and `demo-*` on
this repo, which can push to ECR and read the dashboard passcode — nothing else. Deployment
itself is `tenant.sh sync <branch>` against the dashboard API, exactly what a human would do.

Pushes to `main` deploy the perpetual tenant, and deliberately cannot create it: CD makes
ephemeral environments only, so a missing `t-main` is an error rather than a surprise
long-lived one.

### Shipping from a fork
A fork ships to the same registry and the same control plane, so it needs three things:

1. Its `owner/repo` added to `github_actions_trusted_repos` (`infra/terraform/variables.tf`),
   then `terraform apply`. OIDC subjects name the repository, so the role refuses a fork until
   it is listed. Forks are trusted for `workshop-*` and `demo-*` only: `main` is the golden app
   and the perpetual environment, which this repo owns.
2. The same two Actions settings this repo has — the `AWS_ROLE_ARN` secret and the
   `AWS_ACCOUNT_ID` variable. GitHub also disables Actions on new forks; turn them on.
3. A `TENANT_PREFIX` repository **variable**, e.g. `gtm`. Without it, a `demo-derek` branch in
   either repo means tenant `derek` — the same namespace, database and hostname, redeployed
   out from under whoever is using it, with no branch mismatch for the dashboard to catch.
   With it, the fork's `demo-derek` is tenant `gtm-derek` at `t-gtm-derek.demo.otterworks.app`,
   and its images are tagged `tenant-gtm-derek` rather than colliding on `tenant-derek`.

What a fork changes is service **images**. The runner deploys from the tree in its own image
and checks out the tenant's branch from this repo, which a fork's branch is not in, so charts
and deploy scripts come from `main` here. Changing those is a PR upstream, not a fork branch.

## Provisioning tenants without cluster access
`scripts/tenant.sh` is the dashboard's API from a shell — for people who provision demos, and
for the agent platforms that do it on their behalf:

```bash
tenant.sh checkout derek              # -> branch workshop-derek, 8h TTL
tenant.sh status derek
tenant.sh checkin derek
tenant.sh list
tenant.sh sync workshop-derek         # what CD runs: redeploy, creating if absent
tenant.sh persist main false          # return the perpetual tenant to the TTL regime
```

It needs only `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for the `de-demo-provisioner`
IAM user (`infra/terraform/iam_provisioner.tf`) — no kubeconfig, no passcode handling. That
user can perform exactly one AWS action: read the dashboard passcode from Secrets Manager
(`otterworks/<env>/dashboard/passcode`). The deploy itself runs as a runner Job under the
control plane's IRSA role, so the authority to create namespaces, databases, DNS records and
IRSA trust stays in the cluster and never sits on a long-lived key. A leaked provisioner key
can create and destroy demo tenants; it cannot reach the account.

Rotate by minting a new access key and deleting the old one — the grant is on the user, so
nothing else changes. Rotating the *passcode* is a `helm upgrade` plus one
`aws secretsmanager put-secret-value`; see the comment in `iam_provisioner.tf`.

## Scale to high-tens
Autoscaling (Karpenter / raised node-group max), VPC **prefix delegation** (avoid pod-IP
exhaustion), PgBouncer for RDS connection limits, single shared managed services with in-resource
namespacing (Scope A; see `docs/plan-B-consolidation.md` for the future 1-bucket/1-table
collapse). Details in `docs/architecture.md` §7.

## Status
This is being built incrementally; see the PR description for what is live vs designed.

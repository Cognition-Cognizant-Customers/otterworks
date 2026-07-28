# Demo Ops Dashboard — API contract

The dashboard is a **Next.js** app (server + UI in one deployable) in namespace
`otterworks-platform`, served at `https://ops.otterworks.app`. All routes below are Next.js
**server** route handlers (`app/api/...`) — enforcement is server-side, never trust the client.

## Auth (passcode, server-side)
- `POST /api/auth/login` `{ passcode }` → sets an HttpOnly, Secure, SameSite=Strict signed
  session cookie (`ow_ops_session`, ~8h). **Constant-time compare** against the passcode from
  env `DASHBOARD_PASSCODE` (mounted from the `demo-ops-dashboard` Secret). **Rate-limit**:
  max 5 attempts / IP / 15 min, exponential backoff, audit `login_ok`/`login_fail`.
- `POST /api/auth/logout` → clears cookie.
- **Every** other `/api/*` handler calls `requireSession()` first (401 if missing/invalid).
  A Next.js `middleware.ts` also gates all non-login routes.

## Tenants
- `GET /api/tenants` → `Tenant[]` — control-table items **joined with live cluster state**
  (namespace phase, ready/total pods, per-service status, url). Cached ~5s.
- `GET /api/tenants/:id` → `Tenant` + `pods[]` + recent `audit[]`.
- `POST /api/tenants/checkout` `{ id?, branch, owner, tier?, ttl?, image_tag?, persistent? }` →
  atomic lock (409 if taken), upsert `TENANT#`, enqueue a **deploy runner Job**, status
  `deploying`. Returns the `Tenant`. `ttl: "never"` and `persistent: true` mean the same thing
  and are accepted only for an id in `PERPETUAL_TENANT_IDS` (403 otherwise).
- `POST /api/tenants/:id/redeploy` `{ branch?, image_tag? }` → enqueue a **deploy runner Job**
  for a tenant that is already checked out. This is the CD entry point. Keeps the owner, the
  branch and the **remaining TTL** — a push must never extend an environment's life. 409 if the
  tenant is `free`/`draining`, if `branch` is not the branch the tenant was checked out from, or
  if a deploy is already running for it.
- `POST /api/tenants/:id/checkin` → enqueue **teardown runner Job**, status `draining`.
  409 for a persistent tenant; clear the flag first (there is deliberately no force flag).
- `POST /api/tenants/:id/extend` `{ ttl }` → bump `expires_at`.
- `POST /api/tenants/:id/persist` `{ persistent, ttl? }` → mark a tenant perpetual, or return it
  to the TTL regime with `ttl` (default `24h`). Setting the flag is allowed only for an id in
  `PERPETUAL_TENANT_IDS`; clearing it is always allowed. The flag and `expires_at` are written
  in one update.
- `POST /api/tenants/:id/inject` `{ scenario }` → drive the bug catalog via a runner Job.
  409 for a persistent tenant: the perpetual environment exists precisely not to be broken.
- `POST /api/tenants/:id/reset` → clear injected scenarios. Allowed for every tenant, including
  the perpetual one, because it only restores.

## Reaper
- `GET /api/reaper` → `CONFIG#reaper`.
- `PUT /api/reaper` `{ schedule_cron, grace_seconds, enabled, sweep_orphans, suspend_idle,
  idle_after_seconds, sweep_infra, sweep_infra_delete }` → update config
  (the reaper CronJob reads this each run; changing the cron also patches the CronJob schedule).
- `GET /api/reaper/orphans` → resources with no matching tenant (preview before sweep).

## Audit
- `GET /api/audit?limit=100` → recent events across tenants.

## Types (shared `lib/types.ts`)
```ts
type TenantStatus = "free"|"reserved"|"deploying"|"active"|"draining"|"error";
interface Tenant {
  id: string; status: TenantStatus; owner?: string; branch?: string; tier: "A"|"B";
  imageTag?: string; url?: string; apiUrl?: string; dbName: string; namespace: string;
  createdAt: number; expiresAt: number; lastSeenAt: number; note?: string;
  // Perpetual: exempt from the reaper and from idle-suspend, not checkin-able
  // and not injectable. At most one tenant (`main`) is normally persistent.
  persistent?: boolean;
  live?: { phase: string; readyPods: number; totalPods: number;
           services: { name: string; ready: boolean; restarts: number }[] };
}
```

## How actions execute (runner Jobs)
The web pod stays light: mutating actions create a Kubernetes **Job** (`otterworks-platform`
ns) from the `otterworks-demo-runner` image, which carries the repo + `aws/kubectl/helm/
terraform/jq` and runs `scripts/deploy-tenant.sh` / `teardown-tenant.sh` / `inject-bug.sh`.
The Job uses the dashboard's IRSA role + a ClusterRole that can manage `otterworks-*`
namespaces. Job name = `deploy-<id>-<epoch>` / `teardown-<id>-<epoch>`; logs stream back via
`GET /api/tenants/:id` (reads Job pod logs). The runner reads/writes the control table so
status transitions survive even if the web pod restarts.

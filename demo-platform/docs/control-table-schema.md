# Control-plane state store — `otterworks-demo-control` (DynamoDB)

The **single source of truth** for the demo platform. It is durable and **independent of any
ephemeral tenant** — tearing a tenant down, cycling nodes, or losing the cluster never loses
this state. On-demand billing, Point-In-Time-Recovery enabled.

Single-table design. `PK` (partition key) + `SK` (sort key), both strings.

## Item types

### Tenant registry — `PK=TENANT#<id>`, `SK=META`
```
id            string   # sanitized attendee id (RFC-1123), e.g. "a01"
status        string   # free | reserved | deploying | active | draining | error
owner         string   # who checked it out (free-form facilitator label)
branch        string   # otterworks git branch mapped to this tenant (workshop-<id>)
tier          string   # A | B
image_tag     string?  # optional pinned image tag
url           string?  # https://t-<id>.demo.otterworks.app
api_url       string?  # https://api-t-<id>.demo.otterworks.app
db_name       string   # otterworks_<id>
namespace     string   # otterworks-<id>
created_at    number    # epoch seconds
checked_out_at number?
expires_at    number    # epoch seconds (TTL) — reaper compares against now
last_seen_at  number    # last reconcile timestamp
note          string?
persistent    bool?     # perpetual tenant: never reaped, never idle-suspended

# written by the idle scan only (demo-platform/reaper/idle-suspend.sh)
req_count     number?  # ingress request counter at the last scan
idle_since    number?  # epoch seconds the counter was first seen at req_count
was_running   number?  # 1 if the tenant had replicas up at the last scan, else 0
```
`expires_at` is also the DynamoDB **TTL attribute** (informational; the reaper is the actor).

`persistent` is the perpetual marker, and it is a boolean rather than a very distant
`expires_at` because the two say different things: an expiry ten years out is indistinguishable
from a typo, while the flag is something the reaper, the idle scan and the dashboard can all
read and refuse to act on. A perpetual tenant still carries an expiry (now + 10y) so that a
reaper reading only the expiry — an older build, or a hand-run script — still does no harm.
Absent means false everywhere. The dashboard only sets it for ids in `PERPETUAL_TENANT_IDS`
(`main`), because a perpetual tenant bills forever.

`was_running` is how a wake is detected. Nothing on the wake path writes to this table —
`tenant-scale.sh up`, dashboard check-out and a manual `kubectl scale` all only touch
Deployments — so a woken tenant still carries the `idle_since` it had before it was
suspended. The `0 -> running` transition is the reaper's only evidence that someone came
back, and it restarts the clock; without it the next pass would scale the tenant straight
back down.

### Checkout lock — `PK=LOCK#<id>`, `SK=LOCK`
Written with `ConditionExpression="attribute_not_exists(PK)"` for **atomic checkout**. Holds
`owner`, `acquired_at`, and a short `lock_ttl` (DynamoDB TTL auto-expiry to avoid stuck locks).

### Reaper config — `PK=CONFIG#reaper`, `SK=CONFIG`
```
schedule_cron       string   # e.g. "*/15 * * * *"
grace_seconds       number   # extra grace beyond expires_at before reaping
enabled             bool
sweep_orphans       bool     # also GC resources with no matching TENANT# item
suspend_idle        bool     # scale tenants with no ingress traffic to zero
idle_after_seconds  number   # how long zero traffic must last before suspending
sweep_infra         bool     # also run the AWS-layer sweep (report-only on its own)
sweep_infra_delete  bool     # let that sweep actually delete what it finds
updated_at          number
updated_by          string
```

### Audit event — `PK=AUDIT#<id>`, `SK=<epoch_ms>#<action>`
Append-only. `action` ∈ {checkout, checkin, extend, redeploy, persist, deploy_ok, deploy_fail,
reap, inject, reset, suspend, login_ok, login_fail}. Attributes: `actor`, `detail`, `ts`.
`redeploy` is a deploy into a tenant that was already up (continuous delivery), as distinct
from the `checkout` that created it.
`suspend` is written by the idle scan when a tenant is scaled to zero; unlike `reap` it
destroys nothing, so a suspended tenant is still checked out and still in the table.

## Access patterns
- List all tenants: `Query`/`Scan` `begins_with(PK,"TENANT#")` (small N; scan is fine at high-tens).
- Get one tenant: `GetItem PK=TENANT#<id>, SK=META`.
- Atomic checkout: conditional `PutItem LOCK#<id>` then upsert `TENANT#<id>`.
- Audit trail for a tenant: `Query PK=AUDIT#<id>` (reverse chronological).
- Reaper reads `CONFIG#reaper` + scans `TENANT#` for expired items.
- Every flag is absent-means-false in the reaper, so the item is seeded by Terraform
  (`control_table.tf`); without it a fresh platform silently does no reaping and no idle
  suspension. Terraform sets `ignore_changes` on the item, so the dashboard owns it after
  install.

## Reconciliation (dashboard + reaper)
The table is *desired state*; the cluster/AWS is *actual*. On each list/reaper pass we join the
two: mark `active` when pods are Ready, `error` on crashloops beyond the golden-planted
admin-service, and flag **orphans** (live namespace/DB/S3 prefix/Dynamo partition with no
`TENANT#` item) for the sweeper.

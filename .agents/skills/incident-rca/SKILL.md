---
name: incident-rca
description: Repo-specific mechanics for reproducing, investigating, and verifying the seeded incident scenarios in OtterWorks. Covers the incident harness targets, the four scenario ids, where each chaos control lives, and the tenant-safety rules.
---

# Incident RCA — OtterWorks

Repo-specific mechanics for working the seeded chaos scenarios. Companion to
the DAST harness (`.agents/skills/dast-remediation/SKILL.md`) — the two share
the same shape: probes through the gateway, a report, a verification loop.

## What the harness is

`incidents/` reproduces the four seeded incidents as the **user** experiences
them: every probe drives the symptom endpoint through the API gateway of a
*running* deployment and reaches a verdict from the response alone — never by
reading the chaos flag. `incidents/scenarios.yaml` is the scenario spec: the
owning service, the symptom, the runbook, and the endpoint each probe drives.

## Commands

```bash
make incident-list                                        # the four scenarios
make incident-inject SCENARIO=<id> INCIDENT_TARGET=<url>  # set one chaos flag
make incident-probe  SCENARIO=<id> INCIDENT_TARGET=<url>  # reproduce the symptom
make incident-verify SCENARIO=<id> INCIDENT_TARGET=<url>  # the resolution proof
make incident-reset  INCIDENT_TARGET=<url>                # clear every chaos flag
```

`INCIDENT_TARGET` defaults to `http://localhost:8080`. Reports land in
`incidents/reports/incident-report.{json,md}` (gitignored) on every exit path.
Exit codes: `0` every scenario PASS, `1` a symptom reproduced (FAIL), `2`
target unreachable / config error, `3` INCONCLUSIVE — no verdict, which is
never a pass.

`incident-verify` proves two things at once: the symptom is gone **and** a
legitimate request on the same path still succeeds (a well-formed 2xx
response) — a fix that refuses everybody reports INCONCLUSIVE, not PASS. A backend the gateway reports as
down (502/503/504) is INCONCLUSIVE too. The latency scenario asserts a
threshold (`INCIDENT_LATENCY_THRESHOLD_MS`, default 2500) and records the
measured value in the report.

## The scenarios

| Scenario id | Service | Symptom endpoint | Runbook |
|---|---|---|---|
| `search-service:suggest_500` | search-service | `GET /api/v1/search/suggest?q=<prefix>` → 500 | `docs/runbooks/search-suggest-500.md` |
| `file-service:upload_s3_error` | file-service | `POST /api/v1/files/upload` → 500 (NoSuchBucket) | `docs/runbooks/file-upload-failure.md` |
| `document-service:slow_queries` | document-service | `GET /api/v1/documents/` → 3-5s injected latency | `docs/runbooks/document-service-slow.md` |
| `notification-service:consumer_strict_schema` | notification-service | `POST /api/v1/files/{id}/share` → no notification in `GET /api/v1/notifications` | `docs/runbooks/notification-processing-failure.md` |

The notification scenario is **local-stack only**: tenant deployments wire
SNS/SQS eventing off by design (`scripts/deploy-tenant.sh` sets
`T_WIRE_EVENTING=false`), so on a tenant a share never emits an event and the
missing notification is a false FAIL, not this incident. The other three
scenarios work on any target you may inject.

## Where each control lives

| Control | Where |
|---|---|
| Flag store | Redis keys `chaos:<service>:<scenario>`, 10-minute TTL |
| Inject / reset API | `POST` / `DELETE /api/v1/admin/chaos` — `services/admin-service/app/controllers/api/v1/admin/chaos_controller.rb` (JWT at the gateway; `X-Chaos-Secret` only when `CHAOS_SECRET` is configured) |
| Per-tenant injection | `scripts/inject-bug.sh <ATTENDEE_ID> <scenario|reset>` (catalog: `scripts/bug-catalog.yaml`) |
| Synthetic alert traffic | `services/admin-service/app/services/chaos_probe_service.rb` |
| search chaos path | `services/search-service/app/api/search.py` (suggest handler) |
| file chaos path | `services/file-service/src/handlers.rs` (upload) |
| document chaos path | `services/document-service/app/api/documents.py` (`_maybe_inject_latency`) |
| notification chaos path | `services/notification-service/.../consumer/SqsConsumer.kt` (strict parser) |
| Dashboards | `observability/grafana/dashboards/chaos-scenarios.json`, `incident-overview.json` |

## Targets and tenant safety

| Target | URL | Use |
|---|---|---|
| Local stack | `http://localhost:8080` | after `make up`; the default |
| Your tenant | `https://api-t-<id>.demo.otterworks.app` | after `scripts/deploy-tenant.sh <id>` |
| Perpetual tenant | `https://api-t-main.otterworks.app` | **GET-only** |

**`t-main.otterworks.app` and any tenant you did not deploy are GET-only.**
Injection writes a chaos flag into the target's Redis and the probes register
accounts and upload files into its database — on t-main (never reaped) that
state stays forever, and on someone else's tenant it lands in the middle of
their demo. Inject, probe, and verify only against the local stack or a
tenant you deployed yourself. Always go through the gateway on port 8080 —
hitting a backend port directly bypasses the edge the user experiences.

The local Java builds (`auth-service`, `notification-service`) pull from Maven
Central; if that is rate-limited in your environment, retry the build or run
against a tenant you deployed instead.

## Working an incident

1. Reproduce: `make incident-probe SCENARIO=<id>` — FAIL plus the evidence in
   `incidents/reports/incident-report.md` is the reproduction.
2. Investigate with the runbook named in the report and the chaos-path file
   above.
3. Resolve: clear the flag (`make incident-reset`) or fix and redeploy the
   owning service.
4. Prove it: `make incident-verify SCENARIO=<id>` must report PASS.

## Golden-app policy

`main` is the durable before-state: the chaos code paths are planted on
purpose (see `AGENTS.md`). Never remove a chaos code path or "fix" a seeded
scenario on `main` — resolutions are demonstrated by clearing the flag, or by
fixes on a branch. Adding a probe to `incidents/harness/probes/` is always
welcome.

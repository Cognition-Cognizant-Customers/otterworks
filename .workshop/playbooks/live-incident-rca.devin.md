# Playbook: Diagnose a live OtterWorks incident and prove the fix

## Overview

Use this when a **running** OtterWorks environment is misbehaving and the job is
to find out why, fix the service that owns the behavior, and prove the symptom is
gone. The point is not to read code until a plausible cause appears — it is to
reproduce the symptom against a running system first, so that the fix has a
before-state to be measured against.

The one guiding principle: **an incident is closed when the check that
reproduced it fails to reproduce it.** A green test suite, a sensible diff, and a
convincing explanation are all necessary and none of them are sufficient.

## Required from user

- **The symptom**, as a user would report it — the endpoint or screen, what they
  see, and roughly when it started.
- **The environment** it was seen in: the local stack (`make up`), a tenant
  (`https://api-t-<id>.demo.otterworks.app`), or the golden app
  (`https://t-main.otterworks.app`, which tracks `main`).
- **Write authority**, explicitly. The golden app and other people's tenants are
  **read-only, GET-only**: a scan or a signup there lands in someone's live demo.
  Reproduce and fix in the local stack or your own tenant.

## Procedure

1. **Reproduce before reading.** Hit the failing path and capture the actual
   request and response, status code, and latency. Keep that pair — it is the
   before-state, and without it "fixed" is an opinion. If the symptom does not
   reproduce, say so and stop: the next step is narrowing the environment, not
   changing code.
2. **Separate environment from code.** OtterWorks can fail for three different
   reasons and they need different fixes:
   - a **chaos flag** in Redis (`chaos:<service>:<scenario>`, set through
     `POST /api/v1/admin/chaos` or `scripts/inject-bug.sh`, TTL-expiring) —
     injected failure, not a defect;
   - a **config override** (e.g. a bucket or endpoint pointing at nothing);
   - a **real defect on `main`**, which is the only one that deserves a PR.
   Check the flag and the config before you touch a service. `GET` the same path
   on the golden app to see whether `main` behaves the same way — a symptom that
   reproduces there is a code defect; one that does not is environmental.
3. **Follow the runbook if there is one.** `docs/runbooks/` holds a runbook per
   known failure mode with the alert, the symptoms, and the investigation steps.
   Several are unfinished (`<!-- TODO -->`). If yours is, complete it from what
   you actually did — that is a deliverable, not a chore.
4. **Localize with evidence, not intuition.** Service logs, the Prometheus
   metrics behind the alert, and the Grafana dashboards
   (`observability/grafana/dashboards/`) narrow it to one service and one code
   path. Name the file and the line before proposing a change.
5. **Fix where the behavior lives.** Fix the service that owns the control or the
   query, not the gateway that surfaced it — an edge patch that hides the symptom
   leaves the failure reachable by another route.
6. **Prove it the same way you reproduced it.** Re-run the exact request from
   step 1 against a restarted service, then re-run the service's own tests and
   `make test-api-flows` to show that legitimate traffic still works. If the repo
   has `make incident-*` targets, use them — they are the machine-checkable form
   of this step.
7. **Land it reviewably.** One PR carrying: the reproduction, the cause with file
   references, the fix, the same check now passing, and the completed runbook.
   Do not commit chaos flags, tenant ids, or credentials.

## Specifications (postconditions)

- The before-state (request, response, status, timing) is recorded in the PR.
- The cause is named as a file and line, and classified as chaos flag, config, or
  code defect.
- The same check that failed now passes against a restarted service, and the
  service's tests plus the API flow suite are green.
- Any runbook covering the symptom is complete: investigation, resolution, and
  post-incident sections filled in from the actual run.
- Nothing was written to the golden app or another tenant.
- Injected chaos is cleared (`DELETE /api/v1/admin/chaos` or
  `scripts/inject-bug.sh <id> reset`) so the environment is left as it was found.

## Advice and pointers

- A blanket failure is not a passing check. If a fix makes the endpoint refuse
  everybody, the symptom is gone and so is the feature — always re-run a
  legitimate request as part of the proof.
- Latency symptoms need a number, not an adjective. Capture the before and after
  timing of the same request; "feels faster" cannot be reviewed.
- Chaos flags expire (600s through the API, 3600s through the script). A symptom
  that disappears mid-investigation was probably injected, and re-injecting is
  how you confirm that rather than a reason to move on.
- The four seeded scenarios — `search-service:suggest_500`,
  `file-service:upload_s3_error`, `document-service:slow_queries`,
  `notification-service:consumer_strict_schema` — each have a runbook and a
  Grafana panel. They are the fastest way to rehearse the loop.
- Read `docs/MULTI-TENANT-RUNBOOK.md` before deploying or tearing down anything.

## Forbidden actions

- Do **not** send anything but `GET` to `t-main.otterworks.app` or to a tenant
  you did not deploy.
- Do **not** claim a fix without re-running the reproduction against the
  restarted service.
- Do **not** "fix" an injected chaos scenario by editing the chaos code path out
  of the service — clear the flag and explain the diagnosis instead.
- Do **not** widen the change beyond the incident. Adjacent cleanup belongs in
  its own PR.
- Do **not** commit anything to `main`; the fix goes on a branch with a PR.

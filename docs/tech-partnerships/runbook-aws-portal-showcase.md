# Demo Runbook — AWS: Legacy Portal → Serverless Showcase (run-of-show)

**Story:** decompose `services/legacy-portal` (Java 11 / Spring Boot monolith,
one process, embedded H2) into API Gateway + three Java 17 Lambdas
(SnapStart, X-Ray) + three DynamoDB tables, and prove behavioral parity with
a 20-step golden HTTP transcript. This file is the run-of-show skeleton;
each migration unit fills in its own beats.

## Roles

- **Parent orchestrator** — owns the namespace lease, the shared
  `terraform init/apply` (remote namespace-keyed S3 state,
  `key=tp-portal/<namespace>/terraform.tfstate`, native S3 locking), the live
  validation window, and the alarm/fault beat.
- **Unit children** — code only; self-verify on unit tests + the local
  fixture (`scripts/tp_portal/fixture/run_fixture.sh`), recons marked
  `run_mode: fixture`.

## Beat A — The monolith today (unit: portal decomposition)

```bash
cd services/legacy-portal && ./scripts/run-onprem.sh    # port 8095, fresh H2
curl -s localhost:8095/health                           # {"status":"UP","service":"legacy-portal"}
```

Show the three bounded contexts in one process: announcements, preferences,
feedback — three schemas, one deployable, one blast radius.

## Beat B — Record the parity contract (unit: portal decomposition)

```bash
python3 scripts/tp_portal/transcript.py record \
  --base-url http://localhost:8095 \
  --out scripts/tp_portal/golden/portal-golden-transcript.json
```

20 steps per `scripts/tp_portal/transcript_spec.json`; the transcript — not
opinion — is the acceptance gate. Contract:
`docs/tech-partnerships/contracts/portal-decomposition.json`.

## Beat C — The decomposed estate (unit: portal decomposition)

- `services/portal-serverless/` — `portal-common/ApiHandler` seam + one
  handler per context; sequential IDs preserved via a `pk=0` counter item;
  declared `ApiException`s keep the monolith's error bodies while unexpected
  exceptions propagate (real invocation errors → alarms and traces work).
- `services/portal-serverless/terraform/` — HTTP API, 3 Lambdas behind
  `live` aliases, 3 PAY_PER_REQUEST tables (PITR on), per-context `Errors`
  alarms + gateway `5xx` alarm, optional alarm→Devin EventBridge webhook,
  optional S3 demo site. All `ow-tp-portal-<ns>-*`, `Project=otterworks-tp`,
  nothing with hourly idle cost.

**Parent:** apply, wait for alias readiness (SnapStart `State=Active` and
`OptimizationStatus=On`), then replay live on fresh tables:

```bash
python3 scripts/tp_portal/reset_tables.py --prefix ow-tp-portal-<ns>
python3 scripts/tp_portal/transcript.py replay \
  --base-url <api_base_url output> \
  --golden scripts/tp_portal/golden/portal-golden-transcript.json \
  --reset-cmd 'python3 scripts/tp_portal/reset_tables.py --prefix ow-tp-portal-<ns>' \
  --out docs/tech-partnerships/recon/portal-decomposition-http-parity.recon.json
```

Expect 20/20 twice (idempotency by actual rerun). Fixture evidence from the
child: `docs/tech-partnerships/recon/portal-decomposition-http-parity-fixture.recon.json`.

## Beat D — Demo page (unit: portal decomposition)

```bash
python3 scripts/tp_portal/demo_server.py     # port 8000, same-origin proxy
```

Three capability panels (announcements / preferences / feedback); the API
base URL lives in localStorage — flip it from the local monolith proxy to the
live `api_base_url` for the cutover moment. No CORS changes touch legacy code.

## Beat E — Fault path & alarms (parent, live window)

_Skeleton — owned by the parent/showcase unit:_ deliberate infrastructure
fault in a throwaway namespace → `AWS/Lambda Errors ≥ 1` → context alarm
OK→ALARM→OK → X-Ray fault trace → (optional) EventBridge → Devin webhook.

## Beat F — Async / event-driven follow-on (unit: portal events)

Narration: in the monolith, downstream processing happened inside the request
or not at all — a downstream failure lost the submission. Here the submission
is durable in a queue, a failure is a visible DLQ depth, and recovery is one
operator command. `POST /api/feedback` keeps its exact golden response
(write-then-publish): the sync write commits first, then a `FeedbackSubmitted`
event goes to the custom EventBridge bus → rule → SQS → projection Lambda →
`feedback-stats` DynamoDB projection.

All commands below run in the parent's live window against the applied
namespace (`NS=demo` shown). Grab the Terraform outputs once:

```bash
cd services/portal-serverless/terraform
API=$(terraform output -raw api_base_url)
BUS=$(terraform output -raw event_bus_name)
QUEUE=$(terraform output -raw feedback_events_queue_url)
DLQ=$(terraform output -raw feedback_events_dlq_url)
QUAR=$(terraform output -raw feedback_triage_quarantine_url)
STATS=$(terraform output -raw feedback_stats_table)
SFN=$(terraform output -raw feedback_triage_state_machine_arn)
```

1. **Green path — event chain end to end.** Submit feedback through the
   gateway and watch the projection converge to the synchronous value:

   ```bash
   curl -s -X POST "$API/api/feedback" -H 'content-type: application/json' \
     -d '{"userId":"demo-user","rating":5,"message":"async demo"}'
   # → 201 and the same body as before this unit (golden transcript unchanged)
   aws dynamodb get-item --table-name "$STATS" \
     --key '{"pk":{"S":"stats"}}'          # cnt / ratingSum grow within seconds
   curl -s "$API/api/feedback/average-rating"   # equals ratingSum/cnt above
   ```

2. **Red path — poison → DLQ → alarm.** Send a malformed event straight onto
   the bus (rating 99 fails validation; max receive count 3, 10s visibility,
   so capture takes ~30–60s — give the beat a minute):

   ```bash
   aws events put-events --entries "[{\"EventBusName\":\"$BUS\",
     \"Source\":\"otterworks.portal.feedback\",\"DetailType\":\"FeedbackSubmitted\",
     \"Detail\":\"{\\\"eventId\\\":\\\"poison-demo-1\\\",\\\"feedbackId\\\":\\\"999\\\",\\\"userId\\\":\\\"demo\\\",\\\"rating\\\":99}\"}]"
   aws sqs get-queue-attributes --queue-url "$DLQ" \
     --attribute-names ApproximateNumberOfMessages   # → "1"
   # The triage workflow independently rejects the same event into its own
   # quarantine queue ($QUAR) — the consumer DLQ counts redrive captures only.
   # CloudWatch alarm ow-tp-portal-demo-feedback-events-dlq-depth flips to ALARM
   # (→ existing alarm→Devin EventBridge rule, same incident path as Beat E)
   ```

3. **Operator replay — nothing lost.** After "fixing" the cause, drain the
   DLQ back onto the main queue with the first-class command:

   ```bash
   python3 scripts/tp_portal/replay_dlq.py --dlq-url "$DLQ" --queue-url "$QUEUE"
   # → {"redriven": 1, "dlq_depth_after": 0}
   ```

   A still-poison message returns to the DLQ after 3 receives — inspect it
   with `aws sqs receive-message --queue-url "$DLQ"`, then delete it once
   triaged; genuine transients are consumed and the projection converges.

4. **Orchestrated workflow — visible retries.** Start a triage execution and
   show the execution history (Standard workflow, browsable in the console):

   ```bash
   aws stepfunctions start-execution --state-machine-arn "$SFN" \
     --input '{"detail":{"eventId":"demo-1","feedbackId":"1","userId":"demo-user","rating":5}}'
   aws stepfunctions list-executions --state-machine-arn "$SFN" --max-results 5
   aws stepfunctions get-execution-history --execution-arn <arn>   # retries/catch visible
   ```

   Rejected/failed triage events land in the dedicated quarantine queue —
   inspect and clear it before hand-off so nothing is left stranded:

   ```bash
   aws sqs receive-message --queue-url "$QUAR"    # full quarantined payload
   aws sqs purge-queue --queue-url "$QUAR"        # reset to clean green
   ```

5. **Async recon (live).** Recompute everything from the estate and gate it:

   ```bash
   python3 scripts/tp_portal/async_recon.py --run-mode live \
     --api-base-url "$API" --queue-url "$QUEUE" --dlq-url "$DLQ" \
     --stats-table "$STATS" --namespace demo \
     --out docs/tech-partnerships/recon/portal-events-async-live.recon.json
   make tp-validate-recon
   ```

Fixture rehearsal of the same script (LocalStack, `run_mode: fixture`, never
live proof) is committed at
`docs/tech-partnerships/recon/portal-events-async-fixture.recon.json`.

## Beat G — Platform showcase (unit: portal showcase)

What the serverless platform gives you that the VM-hosted monolith cannot:
a closed front door, deploys that roll themselves back, an alarm that pages
Devin instead of a human, and a bill that goes to ~$0 when nobody is using it.
Contract: `docs/tech-partnerships/contracts/portal-showcase.json`. All beats
below run in the parent's live window; the child's fixture evidence is
`docs/tech-partnerships/recon/portal-showcase-frontdoor-fixture.recon.json`
(auth-enabled 20/20 replay + 401/403 probes, `run_mode: fixture`).

Grab the outputs once (the token is a sensitive output — never echo it):

```bash
cd services/portal-serverless/terraform
API=$(terraform output -raw api_base_url)
export PORTAL_API_TOKEN=$(terraform output -raw demo_api_token)
CDN=$(terraform output -raw demo_site_cdn_url)
```

### G1 — Front door: the API is closed (401 / 403 / 200 on screen)

```bash
curl -s -o /dev/null -w '%{http_code}\n' "$API/api/announcements"
# → 401   (no credential: the Authorization header is the authorizer's
#          identity source, so the gateway rejects before invoking anything)
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Authorization: Bearer wrong-token' "$API/api/announcements"
# → 403   (wrong credential: authorizer denies)
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $PORTAL_API_TOKEN" "$API/api/announcements"
# → 200
curl -s "$API/health"    # → 200 — the only route left open (health probe)
```

Demo page: open `$CDN` (CloudFront, WAF-attached, HTTPS), paste the API URL
and the token into the header fields, Connect. The token lives in
localStorage (`otterPortalApiToken`) next to the base URL — reset both when
switching acts. Parity with auth (expect 20/20 twice):

```bash
python3 scripts/tp_portal/transcript.py replay \
  --base-url "$API" \
  --golden scripts/tp_portal/golden/portal-golden-transcript.json \
  --unit legacy-portal-showcase/platform-capabilities \
  --reset-cmd 'python3 scripts/tp_portal/reset_tables.py --prefix ow-tp-portal-<ns>' \
  --out docs/tech-partnerships/recon/portal-showcase-frontdoor-live.recon.json
```

Burst shed (WAF rate-based rule, default 300 req/5min/IP, `waf_rate_limit`):

```bash
for i in $(seq 1 400); do curl -s -o /dev/null -w '%{http_code}\n' "$CDN"; done | sort | uniq -c
# → mostly 200, then 403s once the rate rule trips (evaluation lags ~30s)
```

### G2 — Deploy safety: canary that rolls itself back

Good canary → auto-promote (bake 120s; gates = per-context errors + api-5xx):

```bash
python3 scripts/tp_portal/canary.py deploy \
  --function ow-tp-portal-<ns>-feedback \
  --jar services/portal-serverless/feedback-service/target/feedback-service.jar \
  --weight 0.1 --bake-seconds 120
# → "published canary version: N" … gate polls … "PROMOTED: … alias 'live' now 100% vN"
```

Bad canary → auto-rollback (CHAOS_FAULT makes every invocation a real
invocation error — the Errors metric increments; a caught 500 would not).
Publish first, drive traffic, and the alarm does the rollback — the operator
does nothing:

```bash
python3 scripts/tp_portal/canary.py deploy \
  --function ow-tp-portal-<ns>-feedback \
  --env CHAOS_FAULT=invoke-error --weight 0.1 --bake-seconds 180 &
sleep 90   # canary Active + weights applied (SnapStart readiness ~45-60s)
for i in $(seq 1 60); do curl -s -o /dev/null \
  -H "Authorization: Bearer $PORTAL_API_TOKEN" "$API/api/feedback/average-rating"; done
wait
# → "ROLLED BACK: ['ow-tp-portal-<ns>-feedback-errors'] in ALARM -> alias 'live'
#    restored to 100% vSTABLE; canary vN received no further traffic" (exit 2)
python3 scripts/tp_portal/canary.py status --function ow-tp-portal-<ns>-feedback
# → stable version at 100%, no additional weights
# cleanup: clear the fault from $LATEST before the next good deploy
python3 scripts/tp_portal/canary.py deploy --function ow-tp-portal-<ns>-feedback \
  --env CHAOS_FAULT= --weight 0.1 --bake-seconds 120   # promotes clean
```

Alarm evaluation is 60s/1-period: give the rollback beat two minutes of
stage time (alarm, then the alias restore). Terraform ignores alias
routing drift, so canary shifts never fight `terraform apply`.

### G3 — Incident loop: alarm → Devin → audit PR

Apply with the automation webhook (values supplied by the parent; the auth
header is sensitive and lives only in the EventBridge connection):

```bash
terraform apply -var devin_webhook_url=https://api.devin.ai/... \
  -var devin_webhook_auth_header='Bearer <automation-secret>'
```

Every estate alarm (per-context errors, api-5xx, DLQ depth, projection
errors, queue age) is matched by rule `ow-tp-portal-<ns>-alarm-to-devin` on
`CloudWatch Alarm State Change` with `state=ALARM` only — one webhook POST
per OK→ALARM transition, nothing on OK or INSUFFICIENT_DATA. Stage the
incident with the same CHAOS_FAULT canary as G2 (without the rollback
narration), then show:

```bash
aws cloudwatch describe-alarm-history \
  --alarm-name ow-tp-portal-<ns>-feedback-errors --max-records 3
aws events list-rule-names-by-target \
  --target-arn $(aws events list-api-destinations \
    --name-prefix ow-tp-portal-<ns>-devin-webhook \
    --query 'ApiDestinations[0].ApiDestinationArn' --output text)
```

Record the alarm-history entry, the rule invocation, the spawned Devin
session URL, and its audit PR URL. **Do not remediate the incident in this
session** — the spawned session fixes it and leaves the audit PR; that is
the beat.

### G4 — Load and cost: the same profile, both estates

Pinned profile `portal-load-v1` (5-request read mix, 32 workers, 60s) —
never compare numbers taken under different profiles, and never quote a
number that was not measured in this run:

```bash
# after-state: through the closed gateway (token via env)
python3 scripts/tp_portal/load_test.py --base-url "$API" \
  --workers 32 --duration 60 --out load-aws.json
# before-state: the legacy monolith (load it LAST — saturating it reddens
# the before-state page, fine as a beat, confusing mid-parity-demo)
cd services/legacy-portal && ./scripts/run-onprem.sh &   # port 8095
python3 scripts/tp_portal/load_test.py --base-url http://localhost:8095 \
  --workers 32 --duration 60 --out load-monolith.json
```

The gateway stage throttles at 100 req/s steady / 50 burst by default —
below what 32 workers generate — so either raise it for the load window
(`-var stage_throttling_rate_limit=1000 -var stage_throttling_burst_limit=500`,
restore after) or read the report's separate `throttled_429` bucket honestly:
429s are the stage cap, not service errors, and they bound the measured
throughput.

Each report carries p50/p95/p99, error rate, throttled-429 count, and
throughput. The narrative
is the curve shape: the monolith's single process climbs and errors past its
thread pool; the estate scales out flat (confirm with Lambda
ConcurrentExecutions / Duration and gateway Count / Latency for the window).

**Cost math (rates as configured, us-east-1):** idle cost ≈ $0 — every
component is per-request: PAY_PER_REQUEST tables, no provisioned
concurrency, no EC2/RDS/NAT/ALB anywhere in the estate. Per-1k-request
order of magnitude at 1024 MB / ~100 ms average: ~0.1 GB-s × 1k ≈ $0.0017
compute + $0.0002 requests + ~$0.00125 DynamoDB writes-equivalent + $0.001
gateway ≈ **well under a cent per 1k requests**; fill in the measured
GB-seconds and request counts from the run's CloudWatch metrics next to the
always-on monthly cost of the VM the monolith needed. Budget guardrail
(“the platform tells you before the bill does” — the monolith estate had no
equivalent): AWS Budgets on `Project=otterworks-tp`, $25/month default,
80%-actual and 100%-forecast notifications to the estate SNS topic. If the
applying principal lacks `budgets:*`, set `-var enable_budget_guardrail=false`
and declare the gap in the live recon's `unverified_paths`.

### G5 — Hand-off checklist (parent)

- alias `live` at the healthy version, 100%, no additional weights
  (`canary.py status` per context);
- every alarm OK, DLQ and quarantine empty, replay green **with
  credentials**, `terraform plan` clean;
- no CHAOS_FAULT left on any function's `$LATEST`;
- rehearsal namespaces destroyed and proven absent by tag + prefix scan.

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
QUEUE=$(terraform output -raw feedback_events_queue_url)
DLQ=$(terraform output -raw feedback_events_dlq_url)
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
   aws events put-events --entries '[{"EventBusName":"ow-tp-portal-demo-portal",
     "Source":"otterworks.portal.feedback","DetailType":"FeedbackSubmitted",
     "Detail":"{\"eventId\":\"poison-demo-1\",\"feedbackId\":\"999\",\"userId\":\"demo\",\"rating\":99}"}]'
   aws sqs get-queue-attributes --queue-url "$DLQ" \
     --attribute-names ApproximateNumberOfMessages   # → "1"
   # CloudWatch alarm ow-tp-portal-demo-feedback-dlq-depth flips to ALARM
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
     --input '{"eventId":"demo-1","feedbackId":"1","userId":"demo-user","rating":5}'
   aws stepfunctions list-executions --state-machine-arn "$SFN" --max-results 5
   aws stepfunctions get-execution-history --execution-arn <arn>   # retries/catch visible
   ```

5. **Async recon (live).** Recompute everything from the estate and gate it:

   ```bash
   python3 scripts/tp_portal/async_recon.py --run-mode live \
     --out docs/tech-partnerships/recon/portal-events-async-live.recon.json
   make tp-validate-recon
   ```

Fixture rehearsal of the same script (LocalStack, `run_mode: fixture`, never
live proof) is committed at
`docs/tech-partnerships/recon/portal-events-async-fixture.recon.json`.

## Beat G — Platform showcase wrap-up

_Skeleton — filled by the showcase unit (`!tp_aws_4_showcase`)._

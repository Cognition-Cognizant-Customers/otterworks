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

## Beat F — Async / event-driven follow-on

_Skeleton — filled by the events unit (`!tp_aws_3_events`)._

## Beat G — Platform showcase wrap-up

_Skeleton — filled by the showcase unit (`!tp_aws_4_showcase`)._

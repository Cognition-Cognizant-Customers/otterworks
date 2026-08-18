# Portal Serverless — legacy-portal decomposition (tech-partnerships demo)

The AWS "after" state for `services/legacy-portal`: the Java 11 / Spring Boot
monolith decomposed along its three bounded contexts into API Gateway (HTTP
API) + three Java 17 Lambda functions + three DynamoDB tables. Lives only on
`tp-run/*` branches; the golden app is untouched.

## Layout

| Path | What |
|---|---|
| `portal-common/` | Shared Lambda plumbing (event decode, JSON, error contract, `/health`) |
| `announcements-service/` | `GET/POST /api/announcements`, `GET /api/announcements/{id}`, `POST /api/announcements/{id}/publish` |
| `preferences-service/` | `GET/PUT /api/preferences/{userId}` |
| `feedback-service/` | `POST /api/feedback`, `GET /api/feedback?userId=`, `GET /api/feedback/average-rating` |
| `terraform/` | API Gateway, Lambdas (SnapStart, X-Ray), DynamoDB, CloudWatch alarms, EventBridge → Devin webhook, optional S3 demo site |
| `demo-ui/` | Standalone "Otter Portal" page with a switchable API base URL |

Related harness: `scripts/tp_portal/` (golden transcript recorder/replayer,
local demo proxy, table reset).

## Parity contract

`scripts/tp_portal/transcript_spec.json` defines a 20-step HTTP transcript
covering all three contexts, executed against a **fresh store**. Status codes
match exactly; bodies compare as parsed JSON; `createdAt` is validated as an
ISO-8601 instant then normalized; framework validation-error bodies are
status-only. Recon artifacts are `*.recon.json` with `"kind": "recon-report"`.

```bash
# Record golden against the local monolith (fresh H2):
cd services/legacy-portal && SKIP_BUILD=1 ./scripts/run-onprem.sh &
python3 scripts/tp_portal/transcript.py record \
  --base-url http://localhost:8095 \
  --out scripts/tp_portal/golden/portal-golden-transcript.json

# Replay against the live estate (fresh tables):
python3 scripts/tp_portal/reset_tables.py
python3 scripts/tp_portal/transcript.py replay \
  --base-url https://<api-id>.execute-api.us-east-1.amazonaws.com \
  --golden scripts/tp_portal/golden/portal-golden-transcript.json \
  --out scripts/tp_portal/golden/aws-replay.recon.json
```

## Build & deploy

```bash
cd services/portal-serverless
JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 mvn -B package   # jars + unit tests
cd terraform && terraform init && terraform apply             # ow-tp-portal-<ns>-*
```

All resources use the `ow-tp-` prefix and `Project=otterworks-tp` tag, are
serverless/on-demand (no EC2/RDS/NAT/LB), and tear down with
`terraform destroy`. Lambda SnapStart snapshots take a few minutes after
apply — wait for function state `Active` before replaying.

## Demo page

```bash
# Before-state: page + monolith on one origin (no CORS changes to legacy code)
python3 scripts/tp_portal/demo_server.py --port 8000   # http://localhost:8000

# After-state: paste the API Gateway URL (or use the S3 demo_site_url output)
```

The page shows all three contexts; each panel degrades independently, so
killing the monolith reds out everything while breaking one Lambda only reds
out its own panel.

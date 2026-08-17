# OtterWorks — SDLC Tool-Coverage Reference

> **Purpose.** OtterWorks is a polyglot microservices platform (11 backend services across 8
> languages + 2 frontends) built to exercise a realistic enterprise SDLC. This document
> inventories **which software-development-lifecycle capabilities the repo actually demonstrates
> today** versus **what is missing or stubbed**, so it can be used as a demo reference when
> mapping OtterWorks to customer stories.
>
> Findings are based on the actual repo contents (file paths cited) plus a live end-to-end AWS
> deployment performed against account `<AWS_ACCOUNT_ID>` / `us-east-1` (EKS `otterworks-dev`, v1.32).
> Status legend: **Present** = works / demonstrable as-is · **Partial** = present but incomplete,
> stubbed, or config-only · **Missing** = claimed or expected but absent.

## Summary table

| # | SDLC dimension | Status | One-line gap |
|---|----------------|--------|--------------|
| 1 | Source control & branching / PR workflow | **Partial** | Good PR/CI gating, but no `CODEOWNERS` and branch protection is not codified in-repo |
| 2 | CI (build / test) | **Present** | Per-language pipelines with path-based change detection across all 13 build units |
| 3 | CD / deployment | **Partial** | As shipped, **every Helm install fails** (missing `ServiceMonitor` CRD) and charts create **no ConfigMap/Secret**; `deploy-dev.sh` now wires all 15 units from Terraform outputs (12/13 backends + both frontends live, website interactive), but ingress/DNS/TLS still assume uninstalled controllers, there's no GitOps, and `admin-service` is down on an app bug |
| 4 | Infrastructure as Code | **Present** | Two-layer Terraform (platform + app) with remote S3 state; one managed-policy bug found & fixed |
| 5 | Containerization | **Present** | 13 Dockerfiles, non-root/read-only hardening; image scanning is filesystem-only, not image-registry |
| 6 | Testing | **Present** | Unit tests per service + black-box API flow suite + contract tests + testdata harness |
| 7 | Security / SAST / DAST / deps | **Present** | Trivy + Gitleaks + Semgrep + SonarCloud + event-driven auto-remediation; no DAST |
| 8 | Observability — metrics | **Partial** | Prometheus/Grafana configs + `/metrics` + ServiceMonitors exist, but stack is **not deployed to EKS** |
| 9 | Observability — tracing | **Partial** | OTel SDK wired in services + Collector/Jaeger configs, but Collector/Jaeger **not deployed to EKS** |
| 10 | Logging | **Partial** | Structured stdout logging + Fluent Bit → CloudWatch config, but Fluent Bit **not deployed to EKS** |
| 11 | Alerting / incident mgmt | **Partial** | PrometheusRule/Grafana alerts + admin-service incident→Devin flow, but no Alertmanager deployed |
| 12 | Secrets management | **Partial** | IRSA for AWS access is solid; `deploy-dev.sh` now delivers app secrets (DB/JWT/Rails) into a Helm-rendered k8s Secret via a temp values file, but it's still deploy-time injection, not External Secrets Operator / Secrets Manager |
| 13 | Data / ETL | **Partial** | Cron + Python ETL scripts present; README-advertised Airflow/Spark are **absent** |
| 14 | Documentation | **Present** | README, ARCHITECTURE, runbooks, CI/security strategy docs, API route matrix |

---

## 1. Source control & branching / PR workflow — Partial

**Evidence.** `.github/workflows/ci.yml` and `.github/workflows/security-scan.yml` both trigger on
`pull_request → main`, so PRs are the enforced integration point. Contributing conventions are
documented in `README.md` (branch from `main`, open PR, CI must pass).

**Gap / what a demo needs.**
- No `CODEOWNERS` file anywhere in the repo — no automatic review routing or ownership mapping.
- Branch-protection rules (required checks, required reviews) live in GitHub settings, not codified
  in-repo, so they can't be demonstrated or reproduced from the repo alone.
- No PR template (`.github/pull_request_template.md`) or issue templates.

**Demo readiness:** fine to show PR-driven CI gating; add `CODEOWNERS` + a PR template for a
"governed source-control" story.

## 2. CI (build / test) — Present

**Evidence.** `.github/workflows/ci.yml` defines a `detect-changes` job using
`dorny/paths-filter` that fans out to **one job per service/language**, each running the idiomatic
toolchain:
- Go `go vet` + `go test -race` + build (`api-gateway`)
- Java 17 `gradle check` (`auth-service`, `notification-service`), Java 8 `mvn` (`report-service`, legacy)
- Rust `cargo fmt/clippy/test/build` (`file-service`)
- Python `ruff` + `pytest --cov` (`document-service`, `search-service`)
- Node `npm ci/lint/test/build` (`collab-service`, `web-app`)
- Scala `sbt compile/test` (`analytics-service`)
- Ruby `rspec` with a Postgres service container (`admin-service`)
- C# `dotnet build/test` (`audit-service`)
- Angular build (`admin-dashboard`)
- Terraform `fmt/validate` (`infrastructure`)
- Black-box API flow collection check (`api-flow-tests`)

**Gap / what a demo needs.** `admin-dashboard` lint/test are soft-failed (`|| true`). No coverage
gates/thresholds enforced in CI itself (coverage is produced but not asserted; quality gating is
delegated to SonarCloud). No caching strategy notes. Overall this is the strongest dimension — an
excellent **polyglot CI demo**.

## 3. CD / deployment — Partial (works, but app comes up unconfigured)

**Evidence.** `scripts/deploy-dev.sh` orchestrates the full path: terraform apply (platform → app),
`aws eks update-kubeconfig`, namespace create, ECR login, `docker build --platform linux/amd64` +
push for all 13 units, then `helm upgrade --install` per service from `infrastructure/helm/<svc>`.
Charts include deployment, service, ingress, networkpolicy, servicemonitor, serviceaccount.

**Observed reality (live deploy, this session).** EKS `otterworks-dev` was recreated, all 13 images
built and pushed to ECR, and `deploy-dev.sh --skip-terraform` ran `helm upgrade --install` for every
service. **As shipped, `helm install` failed atomically for all 13 services and zero pods, services,
or releases were created** (`kubectl get pods -n otterworks` → `No resources found`;
`helm list -n otterworks` → empty). The application was **not usable over the network** as shipped.
Concrete, reproducible reasons, in the order they bite:

1. **The `ServiceMonitor` CRD does not exist on the cluster — this alone blocks every install.**
   Every chart renders a `ServiceMonitor` (`monitoring.coreos.com/v1`,
   `infrastructure/helm/*/templates/servicemonitor.yaml`, gated only on `.Values.monitoring.enabled`
   which defaults `true`), but `platform/terraform` installs **no Prometheus Operator**, so that CRD
   is absent. Helm fails the whole release:
   `Error: unable to build kubernetes objects from release manifest: ... no matches for kind
   "ServiceMonitor" in version "monitoring.coreos.com/v1"`. Because Helm is atomic per release,
   **not even the Deployment/Service is created.** This is the first and most fundamental blocker.
2. **No ConfigMap/Secret is ever created.** Every `deployment.yaml` does
   `envFrom: [{configMapRef: {name: {{ .Release.Name }}-config, optional: true}}]`
   (`infrastructure/helm/*/templates/deployment.yaml`), but **no chart or script defines that
   ConfigMap or any Secret** (`grep -rl 'kind: ConfigMap|kind: Secret' infrastructure/helm` → none).
   So even once installs succeed, database, S3, Redis, Cognito, and JWT settings are never injected.
   Example blast radius: `services/api-gateway/internal/config/config.go` `Validate()` **fatals** when
   `JWT_SECRET` is unset, and `cmd/server/main.go` calls it at startup — so api-gateway crash-loops.
   Stateful services (auth/admin/audit/etc.) similarly lack DB credentials.
3. **Ingress assumes uninstalled infrastructure.** `values.yaml` sets `ingress.className: nginx` +
   `cert-manager.io/cluster-issuer: letsencrypt-prod` + host `otterworks.workshop.example.com`, but
   `platform/terraform` installs **no ingress-nginx controller and no cert-manager**, and that DNS
   does not resolve. Ingress objects are created but never get an address.
4. **LoadBalancer is the only real external entrypoint.** Only `api-gateway`'s Service is
   `type: LoadBalancer` (others are `ClusterIP`), so an AWS ELB is the practical way in — and it
   only works once the release installs and the pod is configured and healthy (see #1, #2).
5. **NetworkPolicy assumes ingress-nginx.** Charts restrict ingress to the `ingress-nginx` and
   `monitoring` namespaces (`templates/networkpolicy.yaml`). This would block direct LB traffic —
   but note EKS VPC-CNI does **not enforce NetworkPolicy by default** (the addon has no
   network-policy config in `platform/terraform/modules/eks/main.tf`), so the policies are inert
   here. That is itself a gap: the policies give a false sense of enforcement.
6. **No GitOps.** There is no ArgoCD/Flux; deployment is imperative via a shell script. No
   environment promotion model (`dev` only; no `staging`/`prod` tfvars beyond a validation enum).
7. **Image-name drift.** `.github/workflows/docker-build.yml` and Helm `values.yaml` defaults push/
   reference `.../workshop/otterworks-<svc>`, but the provisioned ECR repos and `deploy-dev.sh`
   use `.../otterworks/<svc>`. The deploy script overrides the repo at install time so it works,
   but the CI `docker-build` job targets repositories that don't exist.

**Wiring applied in this PR (best-effort, to prove reachability).** To move from "nothing installs"
to a working core, this PR makes two general, backwards-compatible chart fixes:
- **CRD-safe ServiceMonitor** — guarded with
  `{{- if and .Values.monitoring.enabled (.Capabilities.APIVersions.Has "monitoring.coreos.com/v1") }}`
  so charts install cleanly whether or not a Prometheus Operator is present.
- **Optional ConfigMap + Secret templates** — new `templates/configmap.yaml` and
  `templates/secret.yaml` render from `.Values.config` / `.Values.secrets` (default `{}`, so no
  behavior change for un-set charts); the deployment `envFrom` now also references
  `{{ .Release.Name }}-secrets` (`optional: true`).

With those fixes, **api-gateway** (`JWT_SECRET` supplied) and **auth-service** (RDS URL/user +
`SPRING_DATASOURCE_PASSWORD`/`JWT_SECRET` supplied from Terraform outputs) were deployed and reached
`1/1 Running`. api-gateway's `LoadBalancer` Service provisioned an ELB and an **end-to-end register +
login flow succeeded over the public internet** (`POST /api/v1/auth/register` and `/login` via the
ELB returned `200` with JWTs; the user persisted to RDS and Flyway migrations validated at schema
v4). Config/secret values were passed at deploy time via `helm --set` and are **not** committed.

**Full-stack wiring applied in this PR.** `scripts/deploy-dev.sh` now closes the ConfigMap/Secret gap
for the **whole** stack: it reads the app-infra Terraform outputs (`load_infra_outputs()`) and, per
service (`build_helm_args()`), injects config via `helm --set` and secrets via a locked-down temp
values file (`-f`, so secret values never hit the process arg list) — RDS JDBC/asyncpg
URLs + credentials, Redis host/port, S3 buckets, DynamoDB tables, SNS topic + SQS queue, a shared
`JWT_SECRET` across the gateway and every token-validating service, a Rails `SECRET_KEY_BASE`, plus the
per-service IRSA role ARN. It also (a) exposes each backend Service on its real container port so the
gateway's in-cluster routing (`http://<svc>:<port>`) resolves, (b) bumps memory for the JVM services
(auth/report/notification/analytics) above the namespace default so they stop OOM-restarting, and
(c) deploys `web-app` + `admin-dashboard` as `LoadBalancer`s. No secret values are committed.

**Observed reality after full deploy (live, this session).** All 13 backends + 2 frontends are
installed; **12/13 backends and both frontends reach `1/1 Running`**, and the website is interactive
over the public internet — register, login, and listing documents / reports / files / notifications all
return `200` end-to-end (web-app ELB → api-gateway → service → RDS/DynamoDB). Getting there surfaced
three more **app↔IaC contract gaps**, all fixed in this PR's Terraform:
- **Redis required TLS but every service connects with plain `redis://`.** The ElastiCache group had
  `transit_encryption_enabled = true` hard-coded, so `file-service` / `collab-service` panicked at
  boot (`.expect("failed to connect to Redis")`). Made it a variable defaulting to `false` to match
  the application (`modules/cache`).
- **`notifications` table GSI drift.** The app queries `userId-createdAt-index` (camelCase) but the
  table defined a dead `user-index` on `user_id` (snake_case) that the app never populates — every
  `/notifications` read 500'd. Aligned the GSI to the app contract (`modules/database`).
- **`file-service` IRSA lacked `dynamodb:Scan`.** `list_files` does a filtered `Scan`, but the role
  only granted `Query` → every `/files` list 500'd with AccessDenied. Added `Scan` (`main.tf` IRSA).

**Search backend now wired in-cluster.** `search-service` previously 500'd on `/search` because it
depends on Meilisearch, which the IaC only provisions on ECS (`modules/search`) — not reachable from
the golden all-in-cluster app. `deploy-dev.sh` now deploys a single-replica in-cluster Meilisearch
(`getmeili/meilisearch`, dev mode) and points `search-service` at `http://meilisearch:7700`;
`/search` and `/health/ready` now return `200`.

**file-service advanced tables now created.** folders / versions / shares each need a DynamoDB table
the IaC never defined; only file-metadata was present. Added `otterworks-folders-*`,
`otterworks-file-versions-*` (hash `file_id`, range `version`), and `otterworks-file-shares-*` in
`modules/database`, extended the file-service IRSA policy to cover them, and wired the table names
into the chart. Verified end-to-end: `POST /api/v1/folders` → `201` and `GET /api/v1/folders` lists
it back; `/api/v1/files`, `/api/v1/files/shared` return `200`.

**Intentional (do not "fix").**
- **`admin-service` (Rails) crash-loops** by design: `config/environments/production.rb` calls
  `ActiveSupport::TaggedLogging.logger(...)` (should be `.new(Logger.new(...))`), invalid on
  Rails 7.1 → boot fails. This is a **planted bug** for bug-hunt / remediation labs and is kept on
  the golden app (see `AGENTS.md`). Demos that need a passing admin-service fix it in their variant.

**Remaining, honest gaps.**
- Still needed for a clean CD story: ingress-nginx + cert-manager + Prometheus Operator (per org
  standards, in `platform-engineering-shared-services`); real DNS/TLS; delivery via External Secrets
  Operator rather than `helm --set`; and a GitOps controller (ArgoCD/Flux) with environment promotion.
- Meilisearch runs in-cluster in dev mode (no master key) and its index isn't yet backfilled from
  existing documents/files (the SQS indexer path stays disabled here), so `/search` returns valid but
  empty results until content is indexed.

## 4. Infrastructure as Code — Present

**Evidence.** Two Terraform root modules with **remote S3 state**
(`s3://otterworks-terraform-state`, keys `platform/` and `otterworks/`):
- `platform/terraform` — VPC, EKS (`otterworks-dev`, v1.36), managed node group, core addons
  (vpc-cni/kube-proxy/coredns/ebs-csi), OIDC provider, 13 ECR repos. Modularized under
  `modules/{vpc,eks,ecr}` with `environments/dev.tfvars`.
- `infrastructure/terraform` — RDS Postgres, ElastiCache Redis, DynamoDB, S3, SNS/SQS, Cognito,
  MeiliSearch-on-ECS, CloudWatch log groups, and **IRSA roles per service**
  (`modules/irsa`, least-privilege policies in `main.tf`). Reads platform outputs via
  `terraform_remote_state`.

**Kubernetes version upgrades.** `cluster_version` in `environments/dev.tfvars` is the target, but
EKS moves one minor version per update, so a cluster more than one behind needs an apply per minor
before the tfvars value takes:

```sh
cd platform/terraform
terraform apply -var-file=environments/dev.tfvars -var cluster_version=1.35   # intermediate hop
terraform apply -var-file=environments/dev.tfvars                             # lands on the target
```

Each hop replaces the system node group's node, so ingress is down for the few minutes the
replacement takes — the pool is deliberately a single node. Tenant capacity is Karpenter's and comes
back on its own. Afterwards, re-run `demo-platform/scripts/install-karpenter.sh` if the pinned
Karpenter version is below the floor for the new Kubernetes minor (>= 1.13 for 1.36).

**Bug found & fixed during this deploy.** `platform/terraform/modules/eks/main.tf` attached
`arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicyV2`, which is **not a real AWS managed
policy** — the first apply failed with `NoSuchEntity`. Corrected to `AmazonEBSCSIDriverPolicy`
(fixed in this PR); the re-apply then completed cleanly.

**Gap / what a demo needs.** No CI plan/apply automation (terraform is only `validate`d in CI, run
manually via script). No drift detection, no `tflint`/`checkov` policy scanning, single environment.
Still a strong **IaC / cloud-native provisioning demo**.

## 5. Containerization — Present

**Evidence.** 13 Dockerfiles (`services/*/Dockerfile`, `frontend/*/Dockerfile`). `docker-compose.yml`
applies runtime hardening (`security_opt: no-new-privileges`, `read_only: true`, `tmpfs: /tmp`).
Builds are `--platform linux/amd64` in the deploy script.

**Gap / what a demo needs.** No multi-arch (arm64) image build in CI/deploy. Container **image**
scanning is not wired — `security-scan.yml` runs Trivy in `fs` mode against source, not against
built images in ECR; ECR scan-on-push is defined per org standards but not asserted here. No SBOM
generation / image signing (cosign).

## 6. Testing — Present

**Evidence.**
- Per-service unit tests exercised by CI (Go `_test.go`, Java/Kotlin `gradle check`, Rust
  `cargo test`, Python `pytest`, Scala `sbt test`, Ruby `rspec`, C# `dotnet test`).
- Black-box **API flow tests** in `tests/api/` (auth, file, document, collaboration, websocket,
  search, audit/analytics/report, notification/admin/gateway, degradation, side-effect flows).
- **Contract tests** in `tests/contract/` plus API/event contracts in `shared/openapi/` and
  `shared/events/schemas/`.
- **Testdata harness** under `testdata/` (generated + harness) with namespaced Postgres schemas
  (see `.agents/skills/synthetic-testdata-generation`).

**Gap / what a demo needs.** `shared/proto/` is a **README stub** (no protobuf/gRPC contracts
despite the architecture referencing them); `shared/openapi/` covers only 3 of 11 services. No
enforced coverage thresholds; no load/perf tests; the API flow suite is `--collect-only` in CI
(imports validated, not executed against a live stack).

## 7. Security / SAST / DAST / dependency scanning — Present (standout)

**Evidence.**
- `.github/workflows/security-scan.yml`: **Trivy** (fs, CRITICAL/HIGH, `.trivyignore`),
  **Gitleaks** secret scanning, **Semgrep** (`p/owasp-top-ten`, `p/security-audit`), scheduled weekly.
- **SonarCloud** quality gate (`sonar-project.properties`, `sonarcloud-scan` job) with a robust
  fail-closed wait on the compute-engine task.
- **Event-driven SAST auto-remediation** (`.github/workflows/sast-auto-remediate.yml`): on human
  PRs, Trivy/Sonar findings trigger a **Devin webhook** to auto-fix, with attempt-counting,
  bot-loop prevention, and escalation to a GitHub Issue after `MAX_FIX_ATTEMPTS`.
- Kubernetes network policies in `security/policies/` (default-deny, DNS, namespace egress) and
  Trivy config in `security/scanning/`.

**Gap / what a demo needs.** No **DAST** (e.g., ZAP against a running app). Image-registry scanning
not wired (see §5). The `security/policies/` default-deny NetworkPolicies are not applied by the
deploy path and are inert under default EKS VPC-CNI (see §3.4). This is the best **"CVE
remediation / closed-loop security"** demo in the repo.

## 8. Observability — metrics — Partial

**Evidence.** `observability/prometheus/{prometheus.yml,alerts.yml,recording_rules.yml}`,
6 Grafana dashboards in `observability/grafana/dashboards/`, `/metrics` endpoints in services
(e.g., api-gateway `promhttp.Handler()`), and a **ServiceMonitor** in every Helm chart.

**Gap / what a demo needs.** The metrics stack runs **only in local `docker-compose.infra.yml`** —
`platform/terraform` deploys **no Prometheus Operator** to EKS, so the ServiceMonitor CRDs the
charts emit have no controller to consume them (and would fail to apply if the CRD is absent).
For a cloud demo, deploy kube-prometheus-stack (or AMP + AMG).

## 9. Observability — tracing — Partial

**Evidence.** OpenTelemetry SDK is wired into services (api-gateway `initTracer()` with OTLP
exporter, `OTEL_EXPORTER_OTLP_ENDPOINT` in `docker-compose.yml`); `observability/otel/` (Collector +
sampling config) and `observability/jaeger/` configs exist.

**Gap / what a demo needs.** OTel Collector and Jaeger are **local-compose only**; nothing deploys
them to EKS, and no `OTEL_EXPORTER_OTLP_ENDPOINT` is injected in-cluster (compounded by the missing
ConfigMap, §3). Traces won't flow in the AWS environment until a collector is deployed and wired.

## 10. Logging — Partial

**Evidence.** Structured JSON logging in services (e.g., api-gateway `zerolog`);
`observability/logging/{fluentbit-config.yml,parsers.conf,log-format-spec.md}` define a Fluent Bit →
CloudWatch pipeline; app-layer CloudWatch log groups exist (`infrastructure/terraform/modules/monitoring`,
output `cloudwatch_log_groups`).

**Gap / what a demo needs.** Fluent Bit is **not deployed as a DaemonSet** to EKS by any chart or
the deploy script, so in-cluster logs stay on stdout/`kubectl logs` and are not shipped/aggregated.
No log retention is enforced on the pod log path. Wire Fluent Bit (or CloudWatch Container Insights)
for a real log-aggregation story.

## 11. Alerting / incident management — Partial (differentiated)

**Evidence.** Prometheus alert rules (`observability/prometheus/alerts.yml`) and Grafana alerting
provisioning (`observability/grafana/provisioning/alerting/`). The **admin-service** implements a
full incident flow: `app/models/incident.rb`, `app/services/devin_session_service.rb`,
`app/controllers/api/v1/admin/{alerts,incidents}_controller.rb` — Grafana/webhook alerts are ingested
as incidents and can spawn a **Devin session for auto-investigation**.

**Gap / what a demo needs.** No Alertmanager is deployed (alerts have no delivery path in-cluster),
and the incident→Devin flow depends on admin-service being configured and running (blocked by §3).
This is the core of an **"observability / incident-response with Devin"** demo — strong story,
needs the runtime wired.

## 12. Secrets management — Partial

**Evidence.** **IRSA** is the strong part: `infrastructure/terraform/modules/irsa` mints
least-privilege IAM roles per service bound to k8s service accounts via the EKS OIDC provider, and
Helm annotates the SA with the role ARN (`serviceAccount.roleArn`). MeiliSearch's master key is
stored in **AWS Secrets Manager** (`infrastructure/terraform/modules/search`). RDS master password
is a terraform input (`var.db_password`), set/rotated at apply time.

**Gap / what a demo needs.** There is **no delivery mechanism for application secrets into pods** —
no k8s Secret, no External Secrets Operator, no SSM/Secrets Manager CSI driver. `docker-compose.yml`
uses hard-coded test credentials (`AWS_ACCESS_KEY_ID: test`, `POSTGRES_PASSWORD: otterworks_dev`,
a default `JWT_SECRET`), which is fine locally but is exactly what's missing in-cluster. Add
External Secrets Operator (pulling from Secrets Manager/SSM) for a credible secrets story.

## 13. Data / ETL — Partial

**Evidence.** `etl/` contains Python batch jobs (`scripts/{storage_cleanup_daily,audit_archive_weekly,
user_activity_daily,search_reindex_weekly,analytics_daily}.py`), a `crontab`, `run.sh`, `config.ini`,
and `ETL_UPGRADE_GUIDE.md`. `analytics-service` now persists events + a daily aggregate rollup to a
**durable PostgreSQL store** via Slick/Flyway (`analytics_events`, `analytics_daily_metrics`) and also
has the S3 **data lake** bucket wired (`s3_data_lake_bucket`) — see `services/analytics-service/README.md`.

**Gap / what a demo needs.** `README.md` advertises `etl/airflow/` (DAGs) and `etl/spark/` (Scala
Spark jobs), but **neither directory exists** — ETL is cron-driven Python only. Either build out
Airflow/Spark or correct the README. Good candidate for a **"legacy cron → orchestrated data
pipeline"** modernization demo. The durable analytics store is also the **"before"** for an
**S3 + Apache Iceberg lakehouse** (Glue/Athena) re-architecture, with an old-vs-new reconciliation
check as continuous validation (baseline: `PostgresMetricsRepositorySpec`).

## 14. Documentation — Present

**Evidence.** `README.md`, `ARCHITECTURE.md`, per-incident **runbooks** (`docs/runbooks/*`),
`docs/CI_STRATEGY.md`, `docs/EVENT_DRIVEN_SECURITY.md`, `docs/api-route-matrix.md`,
`docs/flows.md`, lab guides in `docs/labs/`.

**Gap / what a demo needs.** No ADRs (`docs/adr/`). Some docs drift from reality (README claims
shared-services EKS + Airflow/Spark; actual is standalone `platform/terraform` + cron ETL — see
§3, §13). Add ADRs and reconcile README drift.

---

## Mapping strengths → customer demo stories

| Demo story | Lead dimensions | Why it lands |
|------------|-----------------|--------------|
| **Polyglot CI demo** | §2, §6 | Change-detected, per-language pipelines across 8 languages + 13 build units — hard to fake, very relatable |
| **CVE remediation / closed-loop security** | §7 | Trivy/Semgrep/SonarCloud + event-driven Devin auto-fix with escalation is a complete, differentiated loop |
| **Cloud-native IaC provisioning** | §4, §3 | Real two-layer Terraform + EKS + IRSA; live apply, including finding & fixing a managed-policy bug |
| **Observability / incident-response with Devin** | §8–§11 | admin-service incident→Devin auto-investigation is a unique narrative (needs runtime wired) |
| **Legacy modernization** | §13, §2 (report-service Java 8) | Cron ETL and the intentionally-legacy `report-service` are ready-made "before" states |
| **Analytics lakehouse re-architecture** | §13 | Durable PostgreSQL analytics store is the "before" for an S3 + Apache Iceberg (Glue/Athena) migration, with old-vs-new reconciliation as continuous validation |

## Top gaps to fix before OtterWorks is a clean all-around reference

1. **Finish the CD wiring.** This PR made charts installable (CRD-safe ServiceMonitor) and added
   optional ConfigMap/Secret templates, proving api-gateway + auth-service reachable. Still the
   biggest single gap: populate config/secrets for **all** services from Terraform outputs (ideally
   External Secrets Operator, not `helm --set`) so the whole app starts end-to-end (§3, §12).
2. **Deploy the observability stack to EKS** — Prometheus Operator, OTel Collector, Jaeger, Fluent
   Bit / Alertmanager — so metrics/traces/logs/alerts are real in AWS, not just local (§8–§11).
3. **Install ingress-nginx + cert-manager + real DNS/TLS** (ideally shared-services) to make the
   ingress path work, and reconcile the `workshop/otterworks-*` vs `otterworks/*` image-name drift (§3).
4. **Add governance & polish** — `CODEOWNERS`, PR template, ADRs, image scanning/SBOM, DAST, and
   README reconciliation (Airflow/Spark, shared-services claims) (§1, §5, §7, §13, §14).

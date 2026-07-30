# OtterWorks CI Strategy

## Current CI Architecture

OtterWorks uses **3 GitHub Actions workflows** that run on every push to `main` and on all PRs targeting `main`:

### 1. `ci.yml` — Service Build & Test Pipeline

**Trigger:** Push to `main`, PRs to `main`

Uses `dorny/paths-filter` for **change detection** — only services with modified files are built and tested. This keeps CI fast in a monorepo with 13+ services.

| Service | Language | CI Steps | Status |
|---------|----------|----------|--------|
| api-gateway | Go 1.22 | `go vet`, `go test -race`, `go build` | Passing |
| auth-service | Java 17 (Gradle) | `gradle check` | Passing |
| file-service | Rust (stable) | `cargo fmt --check`, `cargo clippy`, `cargo test`, `cargo build --release` | Passing |
| document-service | Python 3.12 (Poetry) | `ruff check`, `pytest --cov` | Passing |
| collab-service | Node.js 20 | `npm ci`, `npm run lint`, `npm test`, `npm run build` | Passing |
| notification-service | Kotlin/Java 17 (Gradle) | `gradle check` | Passing |
| search-service | Python 3.12 | `pip install -r requirements-dev.txt`, `pytest --cov` | Passing |
| analytics-service | Scala/Java 17 (sbt) | `sbt compile`, `sbt test` | Passing |
| admin-service | Ruby 3.3 (Rails) | `db:schema:load`, `rspec` (with Postgres service container) | **Fixed** (was failing due to missing `table_name`) |
| audit-service | C# / .NET 8 | `dotnet restore`, `dotnet build`, `dotnet test` | Passing |
| report-service | Java 8 (Maven) — **LEGACY** | `mvn compile`, `mvn test`, `mvn package` | Passing |
| web-app | Node.js 20 (Next.js) | `npm ci`, `npm run lint`, `npm test`, `npm run build` | Passing |
| admin-dashboard | Node.js 20 (Angular) | `npm ci`, `npm run lint \|\| true`, `npm test \|\| true`, `npm run build` | Passing |
| infrastructure | Terraform 1.7 | `terraform fmt -check`, `terraform init`, `terraform validate` | Passing |

### 2. `security-scan.yml` — Security Scanning

**Trigger:** Push to `main`, PRs to `main`, weekly schedule (Monday 6AM UTC)

| Job | Tool | Purpose |
|-----|------|---------|
| `dependency-scan` | Trivy v0.36.0 | Scans filesystem for CRITICAL/HIGH CVEs. Skips `report-service` (legacy). Uses `.trivyignore` for acknowledged CVEs. |
| `secret-scan` | Gitleaks v8.21.2 | Full-history secret detection with redaction. |
| `sast` | Semgrep | Static analysis with OWASP Top 10, default, and security-audit rulesets. |

### 3. `docker-build.yml` — Container Build & Push

**Trigger:** Push to `main`, version tags (`v*`)

Builds all 12 service Docker images in a matrix and pushes to ECR (`<AWS_ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/workshop/otterworks-<service>`). Requires `AWS_ROLE_ARN` secret for OIDC-based ECR authentication.

## Current State (Post-Fix)

After PR #31 (this PR):

- **All 14 service CI checks**: Passing
- **Security scans**: All 3 passing (dependency-scan, secret-scan, sast)
- **Docker build**: Requires `AWS_ROLE_ARN` org secret (only runs on push to main/tags)

## CI Strategy Recommendations

### Immediate (No Changes Required)
1. **Change detection is working well** — Only affected services are built on PRs, keeping CI fast (~2-5 min per service vs 30+ min for full monorepo build).
2. **Security scanning is comprehensive** — Trivy + Gitleaks + Semgrep covers dependencies, secrets, and static analysis.
3. **Legacy service isolation** — Report service (Java 8) is correctly skipped by Trivy since it's intentionally outdated for upgrade exercises.

### Short-Term Improvements
1. **Add `concurrency` groups** to cancel stale CI runs when new commits are pushed:
   ```yaml
   concurrency:
     group: ${{ github.workflow }}-${{ github.ref }}
     cancel-in-progress: true
   ```
2. **Pin Semgrep version** in `security-scan.yml` (currently `pip install semgrep` gets latest, which could break unexpectedly).
3. **Add caching** for slow builds:
   - Rust/file-service: `actions/cache` for `~/.cargo` and `target/` (~2 min savings)
   - Scala/analytics-service: Cache `~/.ivy2` and `~/.sbt` (~1 min savings)
   - Python services: Cache pip/poetry virtual environments

### Medium-Term Improvements
1. **Integration test stage** — Add a post-build stage that runs `docker-compose up` with all services and executes the Playwright E2E tests against the running stack. This catches cross-service issues that unit tests miss.
2. **Helm chart validation** — Add `helm lint` and `helm template` validation for all charts under `infrastructure/helm/`.
3. **Required status checks** — Configure branch protection on `main` to require:
   - All service CI jobs that have changes
   - All 3 security scans
   - Devin Review (if enabled)

### Long-Term Improvements
1. **Deploy preview environments** per PR using Helm + a shared dev EKS cluster. Each PR gets its own namespace with all services deployed.
2. **Automated dependency updates** — Enable Dependabot or Renovate for all service dependency files (package.json, Cargo.toml, build.gradle, Gemfile, etc.).
3. **Performance regression testing** — Add benchmark CI steps for critical paths (API gateway routing, document service CRUD, file service upload/download).

## Known CVE Acknowledgements (.trivyignore)

| CVE | Package | Reason |
|-----|---------|--------|

CVE-2026-33195 and CVE-2026-33658 (activestorage) were resolved by upgrading admin-service to Rails 7.2.3.2. Remaining acknowledgements are tracked in `.trivyignore`.

## Running CI Locally

Each service can be tested locally using the same commands as CI:

```bash
# Go — API Gateway
cd services/api-gateway && go test -race ./...

# Java — Auth Service
cd services/auth-service && gradle check

# Rust — File Service
cd services/file-service && cargo test

# Python — Document Service
cd services/document-service && poetry run pytest

# Node.js — Collab Service
cd services/collab-service && npm test

# Ruby — Admin Service (requires Postgres)
cd services/admin-service && bundle exec rspec

# Full local stack
docker compose -f docker-compose.infra.yml -f docker-compose.yml up -d --build
```

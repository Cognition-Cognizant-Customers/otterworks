# Security Remediation Report — `demo-secscan1`

Remediation of the CRITICAL and HIGH findings recorded in `SECURITY_BACKLOG.md`
(branch `devin/1785905553-security-scan-triage`). Re-scans were run with
`trivy fs --config security/scanning/trivy-config.yaml --offline-scan .`
(the same invocation as `make security-scan`, plus `--offline-scan` because the
in-target run crashes on a Maven Central 429 rate limit — see the backlog's
"Scan run details"). Lock files were regenerated with each ecosystem's own tool
(`npm audit fix` / `npm install`, `go get` + `go mod tidy`, `bundle update`,
`pip install -r`), never by hand.

## Fixed findings

| CVE / Advisory | Package | Manifest | Before | After | Test command | Test result | Re-scan proof |
|---|---|---|---|---|---|---|---|
| CVE-2026-59725 / GHSA-r635-g3xr-vw7x | engine.io | services/collab-service/package-lock.json | 6.6.6 | 6.6.7 | `npm test` | 45 passed, 0 failed | `services/collab-service/package-lock.json (npm) — 0` findings; CVE absent |
| CVE-2026-69185 / GHSA-2m8v-j782-fhvr | socket.io-parser | services/collab-service/package-lock.json | 4.2.6 | 4.2.7 | `npm test` | 45 passed, 0 failed | same — 0 findings |
| GHSA-3jxr-9vmj-r5cp, GHSA-mh99-v99m-4gvg, GHSA-rgw5-rvv9-x895 | brace-expansion | services/collab-service/package-lock.json | 2.1.0 / 1.x | 2.1.4 / 1.1.18 | `npm test` | 45 passed, 0 failed | `npm audit` no longer lists brace-expansion |
| GHSA-h67p-54hq-rp68, GHSA-52cp-r559-cp3m | js-yaml | services/collab-service/package-lock.json | 4.1.1 / 3.x | 4.2.1 / 3.14.3 | `npm test` | 45 passed, 0 failed | `npm audit` no longer lists js-yaml |
| PYSEC-2026-2151 | flask | services/search-service/requirements.txt | 3.0.2 | 3.1.3 | `.venv/bin/pytest` | 41 passed | `pip-audit -r requirements.txt` no longer lists flask |
| PYSEC-2026-1605 | marshmallow | services/search-service/requirements.txt | 3.21.1 | 3.26.2 | `.venv/bin/pytest` | 41 passed | pip-audit no longer lists marshmallow |
| PYSEC-2026-1872/1873/2275 | requests | services/search-service/requirements.txt | 2.31.0 | 2.33.0 | `.venv/bin/pytest` | 41 passed | pip-audit no longer lists requests |
| PYSEC-2026-2270 | python-dotenv | services/search-service/requirements.txt | 1.0.1 | 1.2.2 | `.venv/bin/pytest` | 41 passed | pip-audit no longer lists python-dotenv |
| CVE-2026-33202 (CRITICAL), CVE-2026-33174, CVE-2026-66066 | activestorage | services/admin-service/Gemfile (+ lock) | 7.1.6 | 7.2.3.2 | `bundle exec rspec` | 120 examples, 0 failures | `services/admin-service/Gemfile.lock (bundler) — 0` findings; bundle-audit no longer lists activestorage |
| CVE-2026-33176 | activesupport | services/admin-service/Gemfile (+ lock) | 7.1.6 | 7.2.3.2 | `bundle exec rspec` | 120 examples, 0 failures | same — 0 findings |
| CVE-2026-61666 | websocket-driver | services/admin-service/Gemfile.lock | 0.8.0 | 0.8.2 | `bundle exec rspec` | 120 examples, 0 failures | same — 0 findings |
| CVE-2026-25681, CVE-2026-27136, CVE-2026-33814, CVE-2026-39821 | golang.org/x/net | services/api-gateway/go.mod | v0.35.0 | v0.55.0 | `go test ./...` | all packages ok | `services/api-gateway/go.mod (gomod) — 0` findings |
| CVE-2026-56852 | golang.org/x/text | services/api-gateway/go.mod | v0.22.0 | v0.39.0 | `go test ./...` | all packages ok | same — 0 findings |
| GHSA-hrxh-6v49-42gf (+ suppressed CVE-2026-33186, entry removed from `.trivyignore`) | google.golang.org/grpc | services/api-gateway/go.mod | v1.61.1 | v1.82.1 | `go test ./...` | all packages ok | same — 0 findings |
| CVE-2026-59873 (CRITICAL), CVE-2026-59874 | tar | demo-platform/dashboard/package-lock.json | 7.5.11 | 7.5.19 | `npm run build` | Compiled successfully | `demo-platform/dashboard/package-lock.json (npm) — 0` findings |
| CVE-2026-64641, CVE-2026-64645, CVE-2026-64649 | next | demo-platform/dashboard/package.json (+ lock) | 15.5.20 | 15.5.21 | `npm run build` | Compiled successfully | same — 0 findings |
| CVE-2026-45623, GHSA-r28c-9q8g-f849 | postcss | demo-platform/dashboard/package.json (+ lock) | 8.4.38 / 8.4.31 | 8.5.18 (all copies) | `npm run build` | Compiled successfully | same — 0 findings |
| GHSA-f88m-g3jw-g9cj | sharp | demo-platform/dashboard/package-lock.json | 0.34.5 | 0.35.0 | `npm run build` | Compiled successfully | same — 0 findings |
| CVE-2026-69185 | socket.io-parser | frontend/client-app/package-lock.json | 4.2.6 | 4.2.7 | `npm test` (vitest) | 4 passed | `frontend/client-app/package-lock.json (npm) — 0` findings |

Final full re-scan (all manifests, CRITICAL+HIGH threshold):

```
demo-platform/dashboard/package-lock.json    npm      0
etl/requirements.txt                         pip      0
frontend/admin-dashboard/package-lock.json   npm      0
frontend/client-app/package-lock.json        npm      0
services/admin-service/Gemfile.lock          bundler  0
services/api-gateway/go.mod                  gomod    0
services/collab-service/package-lock.json    npm      0
services/document-service/poetry.lock        poetry   0
services/file-service/Cargo.lock             cargo    0
services/legacy-portal/pom.xml               pom      0
services/report-service/pom.xml              pom      0
services/search-service/requirements.txt     pip      0
```

## Hardcoded credentials — `etl/config.ini`

Removed from the committed file and moved to environment variables (loaded from
`/opt/etl/.env`, which `etl/run.sh` already sources — matching how the other
services read secrets from env):

| Secret | Was | Now |
|---|---|---|
| AWS access key + secret key | `[aws] access_key` / `secret_key` in config.ini | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars |
| Database password (`etl_pr0d_2019!`) | `[database] password` | `ETL_DB_PASSWORD` env var |
| MeiliSearch master key (`masterKey`) | `[services] meilisearch_api_key` | `MEILISEARCH_API_KEY` env var |

All five `etl/scripts/*.py` were updated to read these from `os.environ`.
Verification: `gitleaks dir etl --no-banner` → **`no leaks found`**
(previously flagged the AWS-style secret key, the database password, and the
MeiliSearch master key). `python3 -m py_compile etl/scripts/*.py` passes.

## Not fixed — breaking changes (narrowed `.trivyignore` entries added)

| CVE / Advisory | Package | Manifest | Why not fixed | `.trivyignore` review date |
|---|---|---|---|---|
| CVE-2026-59892, CVE-2026-44902 | @opentelemetry/propagator-jaeger, @opentelemetry/sdk-node | services/collab-service/package-lock.json | Fix requires @opentelemetry/sdk-node 0.49.1 → ≥0.217.0, a semver-major SDK rewrite with breaking tracing-setup API changes | 2026-11-01 |
| CVE-2026-47736, CVE-2026-47737 | puma | services/admin-service/Gemfile.lock | Fix requires puma 6.6.1 → ≥7.2.1, a major version bump | 2026-11-01 |
| CVE-2026-48818, CVE-2026-54283 | starlette | services/document-service/poetry.lock | Fix versions 1.1.0/1.3.1 are a major bump; fastapi ^0.110 pins starlette <0.38, so it needs a coordinated FastAPI upgrade with breaking API changes | 2026-11-01 |
| GHSA-82j2-j2ch-gfr8 | rustls-webpki | services/file-service/Cargo.lock | Comes via aws-smithy-http-client → hyper-rustls 0.24 → rustls 0.21; the fixed 0.103.x line needs the rustls 0.23 stack, i.e. an AWS SDK upgrade whose MSRV exceeds the pinned Rust 1.83 toolchain | 2026-11-01 |
| CVE-2026-50170/50171/54266/54267/54268/68945/69151 | @angular/common, @angular/compiler, @angular/core | frontend/admin-dashboard/package-lock.json | Same deferred Angular 17 → ≥19.2.23 major upgrade already suppressed for 5 older Angular CVEs | 2026-11-01 |
| GHSA-qwww-vcr4-c8h2 | react-router | frontend/client-app/package-lock.json | Fix 8.3.0 is a major bump with breaking route API changes | 2026-11-01 |
| CVE-2024-47554 | commons-io | services/report-service/pom.xml | report-service is the intentional legacy Java 8 / Spring Boot 2.5 service kept as-is for the upgrade exercise (AGENTS.md golden-app policy) | 2026-11-01 |
| PYSEC-2026-1383/1384/1385 (flask-cors 4.0.2 → 6.0.0), PYSEC-2026-1845 (pytest 8.1.1 → 9.0.3) | flask-cors, pytest | services/search-service/requirements.txt | Major version bumps; both are sub-HIGH at the configured Trivy threshold (pip-audit-only findings, pytest is dev-only), so no `.trivyignore` entry is needed | — |

Also removed two now-stale `.trivyignore` suppressions whose findings are fixed
by this change: CVE-2026-33195 (activestorage, fixed by Rails 7.2.3.2) and
CVE-2026-33186 (grpc-go, fixed by v1.82.1).

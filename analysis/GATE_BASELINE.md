# Quality Gate Baseline

Inventory of every quality gate that currently exists, what it covers, and where it is
weak. Gates that are configured so that they **cannot fail** are flagged explicitly.

## 1. Change-gated CI jobs (`.github/workflows/ci.yml`)

The pipeline uses `dorny/paths-filter@v3` (`detect-changes` job, ci.yml lines 14–78) so
each job only runs when its component's paths change. Jobs and their commands:

| Job (ci.yml lines) | Trigger paths | What it runs | Weaknesses |
|---|---|---|---|
| `api-gateway` (81–95) | `services/api-gateway/**` | `go vet`, `go test -race -coverprofile`, build | Coverage profile is produced (line 94) but never checked against a threshold or uploaded. |
| `auth-service` (98–114) | `services/auth-service/**` | `gradle check` | `check` runs Spotless + tests; JaCoCo produces reports only — `build.gradle` lines 64–73 configure `jacocoTestReport` with no `jacocoTestCoverageVerification` rule, so coverage cannot fail the build. |
| `file-service` (117–132) | `services/file-service/**` | `cargo fmt --check`, `clippy -D warnings`, `cargo test`, release build | Toolchain floats (`dtolnay/rust-toolchain@stable`, line 126) — results can change without a code change. |
| `document-service` (135–150) | `services/document-service/**` | `ruff check .`, `pytest --cov=app` | `--cov` reports only; no `--cov-fail-under` in the command or in `pyproject.toml` (`[tool.pytest.ini_options]` sets none). |
| `collab-service` (153–168) | `services/collab-service/**` | `npm run lint`, `npm test`, `npm run build` | `npm test` = `jest --coverage` (`package.json`), but `jest.config.js` lines 14–21 set `coverageThreshold` global branches/functions/lines/statements all to **0** — the coverage gate exists and can never fail. |
| `notification-service` (171–187) | `services/notification-service/**` | `gradle check` | No coverage tooling configured in `build.gradle.kts` at all. |
| `search-service` (190–203) | `services/search-service/**` | `pip install -r requirements-dev.txt`, `pytest --cov=app` | **No lint step** (ruff is invoked for search-service by `make lint`, Makefile line 149, but not in CI). Coverage: `.coveragerc` line 7 sets `fail_under = 0` — **cannot fail**. |
| `analytics-service` (206–226) | `services/analytics-service/**` | `sbt compile`, `sbt test` | No lint/format check, no coverage plugin in `build.sbt`. |
| `admin-service` (229–264) | `services/admin-service/**` | `rspec` against a real Postgres service container | SimpleCov is in the Gemfile (`group :test`) but no minimum coverage enforced; RuboCop is a dev dependency (`Gemfile`) but never run in CI. |
| `audit-service` (267–281) | `services/audit-service/**` | `dotnet restore/build/test` | No coverage collection, no analyzers gate. |
| `web-app` (284–299) | `frontend/client-app/**` | `npm run lint`, `npm test`, `npm run build` | `npm test` = `vitest run --passWithNoTests` (`frontend/client-app/package.json` line 10). Exactly one unit test file exists (`src/lib/corporate.test.ts`); if it were deleted the job would still pass. Playwright e2e (`e2e/*.spec.ts`) and Cucumber BDD (`bdd/`) suites exist but are **not run by any CI job**. |
| `admin-dashboard` (302–317) | `frontend/admin-dashboard/**` | `npm run lint \|\| true`, `npm test \|\| true`, `npm run build` | **Cannot fail (lint & test)**: ci.yml lines 315–316 append `\|\| true`. Compounding this, `angular.json` defines no `lint` architect target, so `ng lint` (package.json `"lint": "ng lint"`) is not even runnable as configured — the `\|\| true` hides a permanently broken command. Only the production build can fail. |
| `report-service` (320–335) | `services/report-service/**` | `mvn compile`, `mvn test`, `mvn package -DskipTests` | Tests do run; no coverage, no static analysis, no dependency audit for the most EOL-laden component in the estate. |
| `legacy-portal` (339–353) | `services/legacy-portal/**` | `./mvnw test -B` | Test-only: no lint, no coverage, no package/build verification. |
| `infrastructure` (356–370) | `infrastructure/**` | `terraform fmt -check`, `init -backend=false`, `validate` | Syntax-level only; no plan/policy checks. |
| `demo-platform` (376–400) | `demo-platform/**`, `scripts/**` | dashboard typecheck+lint, shellcheck, four reaper shell test suites | Shellcheck deliberately scoped: comment at lines 393–395 says `scripts/` "has a backlog of findings that would make this gate permanently red, and so ignored" — the repo-root `scripts/` directory is unlinted. |
| `demo-platform-terraform` (403–417) | `demo-platform/**` | fmt/init/validate | Syntax-level only. |
| `api-flow-tests` (420–431) | `tests/api/**`, api-gateway config, compose files, Makefile (filter lines 73–78) | `pip install -r tests/api/requirements.txt`, `python -m py_compile tests/api/*.py`, `pytest tests/api --collect-only -q` | **Cannot catch functional regressions**: the job only byte-compiles and *collects* the tests (line 431); it never runs them against a stack. Actual execution exists only as local `make test-api-flows` (Makefile line 138). |

Structural weakness of change-gating: because every job is guarded by
`if: needs.detect-changes.outputs.<svc> == 'true'`, a change to shared contracts
(`shared/openapi/**`, `shared/events/schemas/**`) or to `tests/contract/**` matches **no
filter** in ci.yml lines 39–78 and triggers no job at all.

## 2. Per-service targets behind the root `Makefile`

- **`make test`** (Makefile lines 114–126): runs each service's native test runner
  sequentially, fail-fast. Weaknesses: the final line (`Makefile` line 125) is
  `cd frontend/web-app && npm test` — **`frontend/web-app` does not exist** (the
  directory is `frontend/client-app`), so a full `make test` run always errors at that
  step regardless of test results. `report-service` and `legacy-portal` are omitted
  (report-service has a separate `test-report`, line 229–230; legacy-portal has no make
  target at all).
- **`make test-coverage`** (lines 128–135): every one of the seven commands ends in
  `|| true` — **this target cannot fail**. It is a report generator, not a gate. It also
  covers only 7 of 12 backend services (no notification, analytics, audit,
  report-service, legacy-portal) and neither frontend.
- **`make lint`** (lines 143–151): golangci-lint, spotlessCheck, clippy, ruff ×2,
  eslint. Weaknesses: line 150 references the nonexistent `frontend/web-app`; no lint
  entries for notification-service, analytics-service, admin-service (RuboCop is
  installed but unwired), audit-service, report-service, or legacy-portal.
- **`make security-scan`** (lines 214–227): Trivy filesystem scan
  (`security/scanning/trivy-config.yaml`: CRITICAL/HIGH, os+library, `.trivyignore`
  honored) plus `npm audit`, `pip-audit`, `bundle-audit`. **Every command ends in
  `|| true` — the target cannot fail** (lines 216, 219, 222, 225). Coverage gaps: audits
  run for only 3 of 12 services, and line 227 explicitly skips the report-service:
  `"=== Report Service (skipped - legacy) ==="` — the component with the oldest
  dependencies is the one never scanned.
- **`make test-api-flows` / `test-api-flows-collect`** (lines 137–141): the real
  execution path for `tests/api/`, requiring a running stack at `localhost:8080`. Local
  only; CI runs collect-only (see §1).

## 3. Black-box API flow tests (`tests/api/`)

Ten flow modules driven through the gateway (`tests/api/conftest.py`,
`DEFAULT_GATEWAY_URL = "http://localhost:8080"`, line 13): `test_auth_flow.py`,
`test_file_flow.py`, `test_document_flow.py`, `test_collaboration_flow.py`,
`test_websocket_collaboration_flow.py`, `test_search_flow.py`,
`test_notification_admin_gateway_flow.py`, `test_audit_analytics_report_flow.py`,
`test_side_effect_flow.py`, `test_degradation_flow.py`.

Coverage: real end-to-end register/login flows, cross-service async fanout
(`side_effect` marker), websocket collaboration, and controlled-degradation scenarios —
markers defined in `tests/api/pytest.ini` (`api_flow`, `gap_revealer`, `side_effect`,
`websocket`, `degradation`).

Weakness: **no CI job ever executes them.** The `api-flow-tests` job stops at
`--collect-only` (ci.yml line 431). Everything these tests would gate (routing,
auth, cross-service contracts at runtime) is enforced only when someone runs
`make test-api-flows` locally against `make up`.

## 4. Contract tests (`tests/contract/`)

A single file, `tests/contract/test_search_contract.py`, validating a **running**
search-service instance against `shared/openapi/search-service.yaml` (docstring, lines
1–11; `SPEC_PATH` line 24; `BASE_URL` defaults to `http://localhost:8087`, line 25).

Weaknesses:

- Only 1 of 3 published OpenAPI specs is contract-tested: `shared/openapi/` also
  contains `document-service.yaml` and `notification-service.yaml` with no
  corresponding tests.
- **Not wired into CI at all**: `tests/contract` appears nowhere in
  `.github/workflows/ci.yml` — no job installs its dependencies (which per its
  docstring differ from `tests/api/requirements.txt`: it needs `pyyaml`, `jsonschema`,
  `requests`) and no `detect-changes` filter (ci.yml lines 39–78) covers
  `tests/contract/**` or `shared/openapi/**`. The `api-flow-tests` job's collect step
  targets only `tests/api` (line 431).
- Requires a live service, so even if invoked in CI as-is it would fail for
  environmental reasons, not contract reasons.

## 5. Event schemas (`shared/events/schemas/`)

Five JSON Schema (draft-07) files: `audit-events.json` (6 event definitions),
`collaboration-events.json` (5), `document-events.json` (4), `file-events.json` (4),
`notification-events.json` (4). Example: `file-events.json` defines
`FileUploadedEvent`/`FileSharedEvent` with `required` field lists and `const`
event-type discriminators.

Weaknesses:

- **Documentation-only — nothing executes them.** A repo-wide search for references to
  `shared/events/schemas` finds only docs: `docs/labs/contract-audit-guide.md` (lines
  13, 25, 43, 53, 64, 80) and `docs/SDLC-COVERAGE.md` (line 231). No producer
  (file-service SNS publish, document-service SNS publish) or consumer
  (notification-service `SqsConsumer.kt`, search-service `app/services/sqs_consumer.py`,
  audit-service `src/Services/SnsConsumer.cs`) loads or validates against these schema
  files, and no test or CI step does either.
- `docs/labs/contract-audit-guide.md` is itself a *manual* audit exercise ("Pick a
  schema… compare against the publisher"), confirming the schemas are checked by hand,
  not by a gate.
- No `detect-changes` filter covers `shared/**` (ci.yml lines 39–78), so editing a
  schema triggers zero CI jobs.

## Summary: gates that cannot fail

| Gate | Why it cannot fail | Evidence |
|---|---|---|
| admin-dashboard CI lint | `npm run lint \|\| true` (and no `lint` target exists in `angular.json`) | `.github/workflows/ci.yml` line 315 |
| admin-dashboard CI test | `npm test \|\| true` | `.github/workflows/ci.yml` line 316 |
| collab-service coverage threshold | all thresholds set to 0 | `services/collab-service/jest.config.js` lines 14–21 |
| search-service coverage threshold | `fail_under = 0` | `services/search-service/.coveragerc` line 7 |
| `make test-coverage` | every command suffixed `\|\| true` | `Makefile` lines 129–135 |
| `make security-scan` | every command suffixed `\|\| true`; report-service skipped outright | `Makefile` lines 216–227 |
| client-app unit tests (degenerate) | `vitest run --passWithNoTests` passes with zero tests present | `frontend/client-app/package.json` line 10 |
| api-flow tests in CI | collect-only, never executed | `.github/workflows/ci.yml` line 431 |
| contract tests | not referenced by any CI job or Makefile target | absent from `.github/workflows/ci.yml` and `Makefile` |
| event schemas | not loaded by any code or test | only referenced from `docs/` |

## Broken tool paths (referenced but nonexistent)

- `frontend/web-app` — referenced by `Makefile` line 107 (`build-web`), line 125
  (`test`), and line 150 (`lint`); the directory does not exist (`frontend/` contains
  `client-app` and `admin-dashboard` only).

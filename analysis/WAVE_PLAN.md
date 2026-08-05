# Wave Plan

Delivery plan for the dispositions in `analysis/MODERNIZATION_STRATEGY.md`, using the
dependency map in `analysis/ESTATE_INVENTORY.md` and the gate inventory in
`analysis/GATE_BASELINE.md`.

Three waves. Units **within** a wave run in parallel (one branch / one demo tenant per
unit), with two ordering exceptions noted in "Cross-wave dependency notes" (1.1 merges
first in Wave 1; 3.3 follows 3.1 in Wave 3). Waves are ordered by blast radius:

- **Wave 1 — leaves and gate repair.** Components no other service calls, plus the
  cross-cutting work that makes the gates in later waves able to fail at all.
- **Wave 2 — components with contract- or flow-covered consumers.** Their consumers are
  known and exercised by `tests/api/` flow tests and (for search) `tests/contract/`.
- **Wave 3 — shared-path components.** `api-gateway` (every request from both frontends)
  and `auth-service` (JWTs consumed by every other service via the shared `JWT_SECRET`),
  plus the legacy-portal decomposition that adds new routes to the shared path.

Ground rules (per `AGENTS.md`):

- **No unit is developed or verified against `main` or `t-main.otterworks.app`.** Every
  unit branches off `main`, is deployed to its own `demo-<id>` tenant via
  `.github/workflows/cd-tenant.yml` / `scripts/deploy-tenant.sh`, and is verified there
  before merge.
- The planted admin-service bug (`config/environments/production.rb`,
  `ActiveSupport::TaggedLogging.logger($stdout)`) is **out of scope for every unit** and
  must survive the admin-service refactor untouched.

## Wave 1 — leaves and gate repair

Nothing else in the estate calls these components (inventory "Depends on other services"
column: report-service and legacy-portal have no inbound callers; admin-dashboard is a
browser app), so a bad change strands only the unit's own tenant. Unit 1.1 is here
because every later wave relies on gates that currently cannot fail.

| Unit | Component | Strategy | Effort | Depended on by (inventory) | Merge gates (must be green) |
|---|---|---|---|---|---|
| 1.1 | Cross-cutting gate repair: fix dead `frontend/web-app` Makefile paths (Makefile lines 107/125/150); remove `\|\| true` from admin-dashboard CI lint/test (ci.yml lines 315–316) and add a real `lint` target to `angular.json`; raise `collab-service` jest `coverageThreshold` and `search-service` `.coveragerc` `fail_under` above 0; add a `detect-changes` filter + CI job for `tests/contract/**` and `shared/**`; promote `make test-api-flows` from collect-only to executed against a compose stack in the `api-flow-tests` job | Refactor (tooling) | Low | Every later unit (their gates) | `demo-platform` CI job (shellcheck), `infrastructure` job, and a full green run of every per-service CI job on the PR (the change touches `ci.yml`, so all jobs trigger); local `make test` completes past the fixed frontend step |
| 1.2 | `services/report-service` | **Rewrite** (Java 8 / Spring Boot 2.5.14 / SpringFox / iText 5 AGPL → Java 17+ / Spring Boot 3.2+; replaces the AGPL iText 5 usage) | High | None (leaf: it *calls* analytics-, audit-, auth-service; nothing calls it) | `report-service` CI job (ci.yml 320–335); `tests/api/test_audit_analytics_report_flow.py` run against the unit's `demo-<id>` tenant; add the service to `make security-scan` (currently skipped, Makefile line 227) and require a clean Trivy CRITICAL/HIGH run |
| 1.3 | `services/legacy-portal` | **Replatform** (containerize into the root `docker-compose.yml` + Helm chart; Spring Boot 2.7.18 → 3.2+, Java 11 → 17; decomposition deferred to Wave 3) | Medium | None | `legacy-portal` CI job (ci.yml 339–353), extended in this unit to also build the image; manual smoke on its `demo-<id>` tenant (no flow test exists — add at least one `tests/api/` flow for its three contexts as part of the unit) |
| 1.4 | `frontend/admin-dashboard` | **Refactor** (real lint/test gates — depends on 1.1 landing first if run late in the wave, otherwise includes it for its own paths; replace mocked client-side auth in `src/app/core/services/auth.service.ts` with the real `POST /api/v1/admin/auth/login`) | Medium | None (browser app; talks to api-gateway) | `admin-dashboard` CI job with `\|\| true` removed (lint + test + build all fail-capable); login verified against the unit's `demo-<id>` tenant with a seeded admin user |
| 1.5 | `services/file-service` toolchain pin | **Refactor** (pin `rust-toolchain.toml` + Dockerfile base image; replace `dtolnay/rust-toolchain@stable` with the pinned version in ci.yml line 126; no code change) | Low | `search-service` (hydrates index entries from it, `app/services/indexer.py`) | `file-service` CI job (fmt, clippy `-D warnings`, `cargo test`, release build) on the pinned toolchain; `tests/api/test_file_flow.py` against the unit's tenant |

Unit 1.5 sits in Wave 1 despite having a consumer because it is a build-reproducibility
pin with zero behavior change; its consumer-facing contract is additionally exercised by
`test_file_flow.py` and `test_search_flow.py`.

## Wave 2 — contract-/flow-covered consumers

These components have known consumers whose integration is exercised by an existing
black-box test (`tests/api/`) or contract test (`tests/contract/`) — which Wave 1 unit 1.1
has made mandatory and executable in CI.

| Unit | Component | Strategy | Effort | Depended on by (inventory) | Merge gates (must be green) |
|---|---|---|---|---|---|
| 2.1 | `services/search-service` | **Refactor** (Flask 3.0.2 → FastAPI per its `TRANSLATION_GUIDE.md`; multi-stage Dockerfile; add the missing CI lint step) | Medium | `frontend/client-app` search UI via api-gateway; consumes events from document-/file-service | `search-service` CI job with ruff added and `fail_under` > 0; `tests/contract/test_search_contract.py` against `shared/openapi/search-service.yaml` on the unit's tenant; `tests/api/test_search_flow.py` |
| 2.2 | `services/analytics-service` | **Refactor** (Akka 2.8.8 / Akka HTTP 10.5.3 BUSL exit → Apache Pekko; drop the `CrossVersion.for3Use2_13` shims where Pekko publishes Scala 3 artifacts) | Medium | `report-service` (`application.properties` line 32) | `analytics-service` CI job (sbt compile + test), extended with a scoverage or lint step per unit 1.1's pattern; `tests/api/test_audit_analytics_report_flow.py` against the unit's tenant (exercises report-service → analytics-service) |
| 2.3 | `services/admin-service` | **Refactor** (platform-consolidation refactor; wire RuboCop into CI; enforce a SimpleCov minimum; **do not touch the planted `production.rb` bug**) | Medium | `frontend/admin-dashboard` via api-gateway (`ADMIN_SERVICE_URL`) | `admin-service` CI job (rspec + Postgres container) with RuboCop and SimpleCov minimum added; `tests/api/test_notification_admin_gateway_flow.py` against the unit's tenant |
| 2.4 | `frontend/client-app` gate hardening | **Retain** + test-gate repair (wire the existing Playwright e2e and Cucumber BDD suites into CI; remove `--passWithNoTests` degeneracy) | Low | End users; depends on api-gateway + collab-service | `web-app` CI job with the e2e/BDD suites executing; `tests/api/test_collaboration_flow.py` + `test_websocket_collaboration_flow.py` against the unit's tenant |

## Wave 3 — shared-path components

A regression here breaks every tenant flow at once (gateway) or every authenticated call
in the estate (auth). They go last, when the flow/contract gates repaired in Waves 1–2
are proven on lower-risk units.

| Unit | Component | Strategy | Effort | Depended on by (inventory) | Merge gates (must be green) |
|---|---|---|---|---|---|
| 3.1 | `services/api-gateway` | **Retain** + integration updates (route/config updates for the rewritten report-service and the replatformed legacy-portal contexts; add a coverage threshold to the existing `go test -race -coverprofile` run) | Low | Both frontends (`client-app` `API_GATEWAY_URL`, `admin-dashboard` `API_URL`); every backend is reachable only through it | `api-gateway` CI job (go vet, `go test -race`, build) with a coverage floor; **full** `make test-api-flows` (all ten flow modules) against the unit's tenant — the gateway is on every flow's path |
| 3.2 | `services/auth-service` | **Retain** + gate hardening (add `jacocoTestCoverageVerification` so coverage can fail the build; no runtime change) | Low | Every service (shared `JWT_SECRET` JWT validation); both frontends | `auth-service` CI job (`gradle check`) with the new JaCoCo verification rule; `tests/api/test_auth_flow.py` plus at least one authenticated flow (`test_document_flow.py`) against the unit's tenant |
| 3.3 | `services/legacy-portal` decomposition | **Decompose** (split the three bounded contexts — announcements, user-preferences, feedback — out of the Wave 1 replatformed monolith; adds new routes to api-gateway, hence Wave 3) | High | None today; new contexts will be consumed via api-gateway | CI jobs for each extracted context (added in-unit, modeled on existing service jobs); the Wave 1.3 flow tests for the three contexts, run against the unit's tenant; `api-gateway` CI job green on the routing change |

## Cross-wave dependency notes

- 1.1 (gate repair) should merge **first inside Wave 1**; every other unit's merge gates
  assume its fixes (executable api-flow job, contract-test CI job, fail-capable
  admin-dashboard job). The remaining Wave 1 units are mutually parallel.
- 3.3 depends on 1.3 (replatform before decomposition) and on 3.1 (gateway routing).
  3.1 depends on 1.2 (the rewritten report-service API surface it must route to).
- Apart from the two intra-wave ordering constraints above (1.1 lands first in Wave 1;
  3.3 follows 3.1 in Wave 3), units within a wave are mutually independent and can be
  executed as one batch of parallel `demo-<id>` tenants.
- Verification limits: units whose behavior depends on SNS/SQS eventing cannot be fully
  verified on an ephemeral tenant (eventing is disabled for tenants per `AGENTS.md`).
  These are identified per-unit in `analysis/RISK_REGISTER.md`.

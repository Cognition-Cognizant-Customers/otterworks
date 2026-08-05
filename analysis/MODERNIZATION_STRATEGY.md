# Modernization Strategy

Classification of each component as **replatform**, **refactor**, **rewrite**, or
**retain**, based only on evidence in this repository. Effort estimates are relative
(low / medium / high) and account for source size, dependency risk, and test coverage
gaps documented in `analysis/GATE_BASELINE.md`.

## Classification table

| Component | Disposition | Reason (evidence) | Effort |
|---|---|---|---|
| `services/api-gateway` | **Retain** | Go 1.22 (`go.mod` line 3), current chi v5 stack, small (~1.7k SLOC), race-enabled tests in CI (ci.yml line 94). No modernization driver in the code. | Low |
| `services/auth-service` | **Retain** | Spring Boot 3.2.4 / Java 17 (`build.gradle` lines 3, 13–15), Flyway 10, JaCoCo + Spotless plugins already wired (`build.gradle` lines 5–7). Current stack. | Low |
| `services/document-service` | **Retain** | Python 3.12 / FastAPI 0.110 / SQLAlchemy 2 async (`pyproject.toml` lines 9–14), Alembic migrations, ruff + pytest-cov in CI (ci.yml lines 149–150). Current stack. | Low |
| `services/collab-service` | **Retain** | Node 20 / TypeScript / Yjs CRDT stack (`package.json`), security overrides actively maintained (`package.json` "overrides"; `DEPENDENCY_NOTES.md`). Current stack. | Low |
| `services/notification-service` | **Retain** | Kotlin 1.9 / Ktor 2.3.9 / JVM 17 (`build.gradle.kts` lines 2, 19; `Dockerfile` line 1). Current stack. | Low |
| `services/audit-service` | **Retain** | .NET 8 (`AuditService.csproj` line 4), an in-support LTS runtime; DynamoDB/S3 access via current AWSSDK v3.7. | Low |
| `services/admin-service` | **Refactor** | Ruby 3.3 / Rails 7.1 (`Gemfile` lines 2, 14) is current, but it is the only Ruby service in an otherwise JVM/Go/Python/Node estate — a bus-factor and platform-consolidation concern rather than a runtime one. Note: contains an intentionally planted production bug (`config/environments/production.rb`, see `AGENTS.md` "Golden app policy") that must NOT be "fixed" as part of modernization. | Medium |
| `services/analytics-service` | **Refactor** | Scala 3.4.0 with Akka 2.8.8 / Akka HTTP 10.5.3 (`build.sbt` lines 1, 16–19). Akka 2.7+ moved to the Business Source License, so staying on this line carries licensing exposure; the `cross CrossVersion.for3Use2_13` shims (`build.sbt` lines 16–19, 27–28) show the Scala 3 / Akka 2.13-artifact mismatch is already being papered over. Candidate: migrate to Apache Pekko or a non-Akka HTTP stack. | Medium |
| `services/file-service` | **Refactor** | Rust/Actix code is modern, but the build is on an **unpinned toolchain**: `Dockerfile` line 1 is `FROM rust:latest`, and no `rust-toolchain.toml` exists in the service. CI also floats on `dtolnay/rust-toolchain@stable` (ci.yml line 126). Pin the toolchain and base image; otherwise retain. | Low |
| `services/search-service` | **Refactor** | Flask 3.0.2 (`requirements.txt` line 1) on Python 3.12 is supported, but it is the odd one out vs. the FastAPI document-service, is a single-stage Docker image (`Dockerfile` line 1, no builder stage), and has no lint step in CI (ci.yml lines 190–203 run only pytest). `TRANSLATION_GUIDE.md` in the service root documents an intended framework translation. | Medium |
| `services/report-service` | **Rewrite** | End-of-life at every layer, self-documented in `pom.xml`: Java 8 (`pom.xml` lines 24–26; runtime image `eclipse-temurin:8-jre`, `Dockerfile` line 14), Spring Boot 2.5.14 with comment "last 2.5.x release. Upgrade target: 3.2+" (lines 10–11), SpringFox 3.0 "(dead project)" (lines 29–30), iText 5.5.13.3 "(AGPL, pre-license-change)" (lines 33–34), commons-lang 2.6 "(EOL)" (lines 35–36), Guava 28.0 "(2019, many CVEs)" (lines 39–40). It is also excluded from `make security-scan` ("skipped - legacy", Makefile line 227). `UPGRADE_GUIDE.md` in the service root lays out the upgrade path. The dependency set (iText 5 AGPL) makes in-place upgrade a license event, not just a version bump. | High |
| `services/legacy-portal` | **Replatform → then decompose** | Self-described "Legacy modular monolith … Runs on a VM today — a rehost and monolith-decomposition 'before' state" (`pom.xml` description, lines 20–23). Java 11 (`pom.xml` lines 26–28) with Spring Boot 2.7.18, annotated "final 2.7.x release (Java 8-19). Upgrade target: 3.2+" (lines 10–11). OSS support for Spring Boot 2.7 ended in 2023. Not integrated into the root `docker-compose.yml`; ships its own `docker-compose.onprem.yml` and `deploy/` scripts. First containerize/replatform alongside the other services, then split the three bounded contexts (announcements, user-preferences, feedback) it bundles. | Medium (replatform) / High (decomposition) |
| `frontend/client-app` | **Retain** | React 18 + Vite + TypeScript (`package.json`), with Playwright e2e, Cucumber BDD, and Capacitor mobile targets already present. Current stack. | Low |
| `frontend/admin-dashboard` | **Refactor** | Angular ^17.3 (`package.json`) is supportable, but quality gates are hollow: CI runs `npm run lint \|\| true` and `npm test \|\| true` (ci.yml lines 315–316), `angular.json` defines **no lint target** (so `ng lint` cannot succeed as configured), and auth is mocked entirely client-side (`src/app/core/services/auth.service.ts` lines 41–78: the real HTTP call is commented out at line 43 and `mockLogin` accepts any email with any non-empty password). Refactor = wire real lint/test gates and real auth integration, not a framework change. | Medium |

## End-of-life / unpinned runtime call-outs

| Component | Finding | Specific evidence |
|---|---|---|
| `services/report-service` | **EOL runtime**: Java 8 | `pom.xml` lines 24–26 (`<java.version>1.8</java.version>` with comment "LEGACY: Java 8 source/target. Upgrade target: Java 17+"); `Dockerfile` line 3 `maven:3.8.7-eclipse-temurin-8`, line 14 `eclipse-temurin:8-jre` |
| `services/report-service` | **EOL framework**: Spring Boot 2.5.14 | `pom.xml` lines 10–11: "LEGACY: Spring Boot 2.5.14 — last 2.5.x release" |
| `services/legacy-portal` | **EOL framework**: Spring Boot 2.7.18 (OSS support ended) on Java 11 | `pom.xml` line 10 comment: "LEGACY: Spring Boot 2.7.18 — final 2.7.x release (Java 8-19). Upgrade target: 3.2+" (version at line 11); lines 26–28 Java 11 |
| `services/file-service` | **Unpinned runtime**: Rust toolchain floats | `Dockerfile` line 1 `FROM rust:latest`; no `rust-toolchain.toml` in `services/file-service/`; CI uses `dtolnay/rust-toolchain@stable` (`.github/workflows/ci.yml` line 126) |
| `services/analytics-service` | License-risk framework: Akka 2.8.x (BUSL-licensed line) | `build.sbt` lines 16–19 (`akka-http 10.5.3`, `akka-actor-typed`/`akka-stream 2.8.8`) |
| `services/report-service` | AGPL dependency: iText 5.5.13.3 | `pom.xml` line 33 comment: "LEGACY: iText 5.5.13.3 (AGPL, pre-license-change)" |

All other runtimes are pinned and in support: Go 1.22 (`services/api-gateway/go.mod` line 3),
Java 17 (`services/auth-service/build.gradle` lines 13–15; `services/notification-service/Dockerfile`
line 1), Python 3.12 (`services/document-service/pyproject.toml` line 9;
`services/search-service/Dockerfile` line 1), Node 20 (`services/collab-service/Dockerfile` line 1;
both frontend Dockerfiles), Ruby 3.3 (`services/admin-service/Gemfile` line 2), .NET 8
(`services/audit-service/AuditService.csproj` line 4), Scala 3.4.0 (`services/analytics-service/build.sbt`
line 1).

## Suggested sequencing

1. **Quick wins (low effort, removes drift):** pin the file-service Rust toolchain; fix the
   dead `frontend/web-app` Makefile paths (Makefile lines 107, 125, 150); make the
   admin-dashboard lint/test gates real (see `analysis/GATE_BASELINE.md`).
2. **report-service rewrite** — the only component that is EOL at runtime, framework, and
   dependency level simultaneously, and the only one excluded from security scanning.
3. **legacy-portal replatform** then decomposition of its three bounded contexts.
4. **analytics-service Akka exit** (Pekko or alternative) on its own track — license-driven,
   not urgency-driven.

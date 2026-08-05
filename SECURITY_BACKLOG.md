# Security Backlog

Tracked CRITICAL/HIGH vulnerabilities that remain deliberately suppressed in
`.trivyignore` because the fix requires a blocked major upgrade. See
`TRIVYIGNORE_AUDIT.md` for the full audit.

**No suppression removed by the audit reintroduced a CRITICAL/HIGH finding** — the
CRITICAL/HIGH scan results with the old and rewritten `.trivyignore` are identical. The
18 removed entries were all no-ops (nonexistent paths, already-remediated versions, or
the non-functional `CVE-2021-*` wildcard).

## Suppressed findings awaiting blocked upgrades

| Service | Package (installed) | Finding | Severity | Fix | Blocked by | Review |
|---|---|---|---|---|---|---|
| frontend/admin-dashboard | @angular/common 17.3.12 | CVE-2025-66035 | HIGH | 19.2.16 | Angular 19+ major upgrade (17.x EOL, no fix) | 2026-11-01 |
| frontend/admin-dashboard | @angular/compiler 17.3.12 | CVE-2025-66412 | HIGH | 19.2.17 | Angular 19+ major upgrade | 2026-11-01 |
| frontend/admin-dashboard | @angular/compiler+core 17.3.12 | CVE-2026-22610 | HIGH | 19.2.18 | Angular 19+ major upgrade | 2026-11-01 |
| frontend/admin-dashboard | @angular/core 17.3.12 | CVE-2026-27970 | HIGH | 19.2.19 | Angular 19+ major upgrade | 2026-11-01 |
| frontend/admin-dashboard | @angular/compiler+core 17.3.12 | CVE-2026-32635 | HIGH | 19.2.20 | Angular 19+ major upgrade | 2026-11-01 |
| services/api-gateway | golang-jwt/jwt/v5 v5.2.1 | CVE-2025-30204 | HIGH | 5.2.2 | Trivial bump — good first fix, unsuppress after `go get` | 2026-09-01 |
| services/api-gateway | otel/sdk v1.24.0 | CVE-2026-24051 | HIGH | 1.40.0 | Newer Go toolchain + coordinated otel/grpc module upgrade | 2026-11-01 |
| services/api-gateway | otel/sdk v1.24.0 | CVE-2026-39883 | HIGH | 1.43.0 | Newer Go toolchain + coordinated otel/grpc module upgrade | 2026-11-01 |
| services/api-gateway | grpc v1.61.1 | CVE-2026-33186 | CRITICAL | 1.79.3 | Newer Go toolchain + coordinated otel/grpc module upgrade | 2026-11-01 |
| services/admin-service | activestorage 7.1.6 | CVE-2026-33195 | CRITICAL | ≥7.2.3 | Rails 7.2+ upgrade; Rails 7.1 pinned for golden-app lab (AGENTS.md) | 2026-11-01 |
| services/admin-service | activestorage 7.1.6 | CVE-2026-33658 | MEDIUM | ≥7.2.3 | Rails 7.2+ upgrade | 2026-11-01 |
| services/document-service | protobuf 4.25.9 | CVE-2026-0994 | HIGH | 5.29.6 | opentelemetry-proto 1.23.0 pins protobuf <5.0; needs OTel Python upgrade | 2026-11-01 |
| services/document-service | starlette 0.37.2 | CVE-2024-47874 | HIGH | 0.40.0 | fastapi ^0.110.0 pin constrains starlette; needs FastAPI upgrade | 2026-11-01 |

## Note on the unsuppressed baseline

Independent of `.trivyignore`, the repo currently has 38 CRITICAL/HIGH findings that are
**not** suppressed (e.g. activestorage CVE-2026-33202 CRITICAL, grpc GHSA-hrxh-6v49-42gf,
next 15.5.20 CVEs in demo-platform/dashboard). CI tolerates them because the
security-scan workflow only gates findings newly introduced by a PR; they are remediation
candidates but out of scope for this ignore-file audit.

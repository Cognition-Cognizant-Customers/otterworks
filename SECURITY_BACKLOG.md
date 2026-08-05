# Security Backlog — `make security-scan` Triage

Triage of the output of `make security-scan` (Makefile line ~214), run 2026-08-05.

## Scan run details

- **Exit code of `make security-scan`: `0`** — every scan command ends in `|| true`, so the target
  exits 0 even with findings (and even when a scanner is missing or crashes).
- Scanner versions used:
  - Trivy **0.73.0** (installed for this run; **not preinstalled** — the first `make security-scan`
    invocation printed `trivy: command not found`)
  - npm **10.9.8** / node v22.23.2 (`npm audit`)
  - pip-audit **2.10.1** (installed for this run; not preinstalled)
  - bundler-audit **0.9.3** with ruby-advisory-db updated 2026-08-05 (installed for this run; not preinstalled)
- The in-target Trivy invocation (`trivy fs --config security/scanning/trivy-config.yaml .`)
  **crashed with `FATAL`** even after installing Trivy: resolving
  `spring-boot-starter-parent` poms from Maven Central returned `429 Too Many Requests`
  (the IP is rate-limit blocked). Because of `|| true` the target still exited 0 and silently
  produced **zero Trivy results**. The Trivy findings below come from a re-run with
  `--offline-scan`, which completes successfully (Maven child-dependency resolution for the two
  `pom.xml` projects is skipped in that mode).
- Trivy config (`security/scanning/trivy-config.yaml`) limits output to **CRITICAL and HIGH** and
  applies `.trivyignore`.

## 1. CRITICAL and HIGH findings

| Severity | CVE / Advisory | Package | Installed | Fixed | Manifest | Scanner |
|---|---|---|---|---|---|---|
| CRITICAL | CVE-2026-33202 | activestorage | 7.1.6 | 7.2.3.1 / 8.0.4.1 / 8.1.2.1 | `services/admin-service/Gemfile.lock` | Trivy (bundler-audit reports it as "Unknown" criticality) |
| CRITICAL | CVE-2026-59873 | tar | 7.5.11 | 7.5.19 | `demo-platform/dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-33174 | activestorage | 7.1.6 | 7.2.3.1 / 8.0.4.1 / 8.1.2.1 | `services/admin-service/Gemfile.lock` | Trivy, bundler-audit |
| HIGH | CVE-2026-66066 | activestorage | 7.1.6 | 7.2.3.2 / 8.0.5.1 / 8.1.3.1 | `services/admin-service/Gemfile.lock` | Trivy, bundler-audit |
| HIGH | CVE-2026-33176 | activesupport | 7.1.6 | 7.2.3.1 / 8.0.4.1 / 8.1.2.1 | `services/admin-service/Gemfile.lock` | Trivy, bundler-audit |
| HIGH | CVE-2026-47736 | puma | 6.6.1 | 7.2.1 / 8.0.2 | `services/admin-service/Gemfile.lock` | Trivy, bundler-audit (High) |
| HIGH | CVE-2026-47737 | puma | 6.6.1 | 7.2.1 / 8.0.2 | `services/admin-service/Gemfile.lock` | Trivy, bundler-audit (High) |
| HIGH | CVE-2026-61666 | websocket-driver | 0.8.0 | 0.8.2 | `services/admin-service/Gemfile.lock` | Trivy, bundler-audit (High) |
| HIGH | CVE-2026-59892 | @opentelemetry/propagator-jaeger | 1.22.0 | 2.9.0 | `services/collab-service/package-lock.json` | Trivy |
| HIGH | CVE-2026-44902 | @opentelemetry/sdk-node | 0.49.1 | 0.217.0 | `services/collab-service/package-lock.json` | Trivy |
| HIGH | CVE-2026-59725 | engine.io | 6.6.6 | 6.6.7 | `services/collab-service/package-lock.json` | Trivy |
| HIGH | CVE-2026-69185 | socket.io-parser | 4.2.6 | 4.2.7 | `services/collab-service/package-lock.json` | Trivy |
| HIGH | GHSA-r635-g3xr-vw7x | engine.io | 6.6.6 | 6.6.7 (`npm audit fix`) | `services/collab-service/package-lock.json` | npm audit |
| HIGH | GHSA-2m8v-j782-fhvr | socket.io-parser | 4.2.6 | 4.2.7 (`npm audit fix`) | `services/collab-service/package-lock.json` | npm audit |
| HIGH | GHSA-3jxr-9vmj-r5cp | brace-expansion | 2.1.0 (and 1.x copies) | 2.1.4 / 1.1.18 (`npm audit fix`) | `services/collab-service/package-lock.json` | npm audit |
| HIGH | GHSA-mh99-v99m-4gvg | brace-expansion | 2.1.0 (and 1.x copies) | 2.1.4 / 1.1.18 (`npm audit fix`) | `services/collab-service/package-lock.json` | npm audit |
| HIGH | GHSA-rgw5-rvv9-x895 | brace-expansion | 2.1.0 (and 1.x copies) | 2.1.4 / 1.1.18 (`npm audit fix`) | `services/collab-service/package-lock.json` | npm audit |
| HIGH | GHSA-h67p-54hq-rp68 | js-yaml | 4.1.1 (and 3.x copy) | 4.2.1 / 3.14.3 (`npm audit fix`) | `services/collab-service/package-lock.json` | npm audit |
| HIGH | GHSA-52cp-r559-cp3m | js-yaml | 4.1.1 (and 3.x copy) | 4.2.1 / 3.14.3 (`npm audit fix`) | `services/collab-service/package-lock.json` | npm audit |
| HIGH | CVE-2026-25681 | golang.org/x/net | v0.35.0 | 0.55.0 | `services/api-gateway/go.mod` | Trivy |
| HIGH | CVE-2026-27136 | golang.org/x/net | v0.35.0 | 0.55.0 | `services/api-gateway/go.mod` | Trivy |
| HIGH | CVE-2026-33814 | golang.org/x/net | v0.35.0 | 0.53.0 | `services/api-gateway/go.mod` | Trivy |
| HIGH | CVE-2026-39821 | golang.org/x/net | v0.35.0 | 0.55.0 | `services/api-gateway/go.mod` | Trivy |
| HIGH | CVE-2026-56852 | golang.org/x/text | v0.22.0 | 0.39.0 | `services/api-gateway/go.mod` | Trivy |
| HIGH | GHSA-hrxh-6v49-42gf | google.golang.org/grpc | v1.61.1 | 1.82.1 | `services/api-gateway/go.mod` | Trivy |
| HIGH | CVE-2026-48818 | starlette | 0.37.2 | 1.1.0 | `services/document-service/poetry.lock` | Trivy |
| HIGH | CVE-2026-54283 | starlette | 0.37.2 | 1.3.1 | `services/document-service/poetry.lock` | Trivy |
| HIGH | GHSA-82j2-j2ch-gfr8 | rustls-webpki | 0.101.7 | 0.103.13 | `services/file-service/Cargo.lock` | Trivy |
| HIGH | CVE-2024-47554 | commons-io:commons-io | 2.6 | 2.14.0 | `services/report-service/pom.xml` | Trivy (offline re-run only; hidden in the normal target — see coverage gaps) |
| HIGH | CVE-2026-64641 | next | 15.5.20 | 15.5.21 / 16.2.11 | `demo-platform/dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-64645 | next | 15.5.20 | 15.5.21 / 16.2.11 | `demo-platform/dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-64649 | next | 15.5.20 | 15.5.21 / 16.2.11 | `demo-platform/dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-45623 | postcss | 8.4.31 | 8.5.12 | `demo-platform/dashboard/package-lock.json` | Trivy |
| HIGH | GHSA-r28c-9q8g-f849 | postcss | 8.4.31 | 8.5.18 | `demo-platform/dashboard/package-lock.json` | Trivy |
| HIGH | GHSA-f88m-g3jw-g9cj | sharp | 0.34.5 | 0.35.0 | `demo-platform/dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-59874 | tar | 7.5.11 | 7.5.18 | `demo-platform/dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-50170 | @angular/common | 17.3.12 | 19.2.23 / 20.3.22 / 21.2.15 | `frontend/admin-dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-50171 | @angular/common | 17.3.12 | 19.2.23 / 20.3.22 / 21.2.15 | `frontend/admin-dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-54266 | @angular/common | 17.3.12 | 20.3.25 / 21.2.17 / 22.0.1 | `frontend/admin-dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-54268 | @angular/common | 17.3.12 | 20.3.25 / 21.2.17 / 22.0.1 | `frontend/admin-dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-68945 | @angular/common | 17.3.12 | 20.3.27 / 21.2.19 / 22.0.2 | `frontend/admin-dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-69151 | @angular/compiler, @angular/core | 17.3.12 | 20.3.27 / 21.2.19 / 22.0.1 | `frontend/admin-dashboard/package-lock.json` | Trivy |
| HIGH | CVE-2026-54267 | @angular/core | 17.3.12 | 20.3.25 / 21.2.17 / 22.0.1 | `frontend/admin-dashboard/package-lock.json` | Trivy |
| HIGH | GHSA-qwww-vcr4-c8h2 | react-router | 7.18.1 | 8.3.0 | `frontend/client-app/package-lock.json` | Trivy |
| HIGH | CVE-2026-69185 | socket.io-parser | 4.2.6 | 4.2.7 | `frontend/client-app/package-lock.json` | Trivy |

Notes:
- pip-audit reported 10 advisories in `services/search-service/requirements.txt`
  (flask 3.0.2 → PYSEC-2026-2151; flask-cors 4.0.2 → PYSEC-2026-1383/1384/1385;
  marshmallow 3.21.1 → PYSEC-2026-1605; requests 2.31.0 → PYSEC-2026-1872/1873/2275;
  python-dotenv 1.0.1 → PYSEC-2026-2270; pytest 8.1.1 → PYSEC-2026-1845). pip-audit does not
  emit severity, and Trivy rates none of them CRITICAL/HIGH (that file shows 0 findings at the
  configured threshold), so they are tracked here as sub-HIGH remediation items rather than
  listed in the table above.
- bundler-audit also reported many advisories with "Unknown"/Medium/Low criticality
  (actionview XSS CVE-2026-33168, activesupport CVE-2026-33169/33170, crass, json, loofah,
  msgpack, net-imap command-injection CVE-2026-47240/47242, nokogiri ×12,
  rails-html-sanitizer, websocket-driver CVE-2026-54463/54464/54465). The Trivy bundler scan
  puts none of these at CRITICAL/HIGH except the rows in the table.

## 2. Affected services (ordered by CRITICAL/HIGH count)

### services/collab-service — 11 (0 CRITICAL, 11 HIGH)
Node/Express + Socket.IO service. Trivy: OpenTelemetry SDK (0.49.1) and propagator-jaeger,
engine.io, socket.io-parser. npm audit adds engine.io/socket.io-parser advisories plus
brace-expansion and js-yaml DoS issues in the dev/test dependency tree. Most are fixable via
`npm audit fix`; the OpenTelemetry bump (0.49.1 → ≥0.217.0) is a breaking change.

### frontend/admin-dashboard — 8 (0 CRITICAL, 8 HIGH)
Angular 17.3.12: 8 active HIGH CVEs across @angular/common/compiler/core (HttpTransferCache
information leak and cache poisoning, XSS via i18n event handlers, DoS). All fixes require
Angular ≥19.2.23 — the same major-version upgrade already deferred in `.trivyignore` for 5
other Angular CVEs, so the suppression list is out of date rather than protective.

### demo-platform/dashboard — 8 (1 CRITICAL, 7 HIGH)
Next.js dashboard: tar 7.5.11 gzip-bomb DoS (CRITICAL), next 15.5.20 SSRF/DoS ×3,
postcss ×2, sharp (libvips CVEs). All have non-major patch releases available.

### services/admin-service — 7 (1 CRITICAL, 6 HIGH)
Rails 7.1.6 stack: activestorage CVE-2026-33202 (unintended file deletion via crafted blob
keys, CRITICAL) plus activestorage RCE via libvips (CVE-2026-66066), Range-request DoS,
activesupport DoS, puma PROXY-protocol memory exhaustion and IP spoofing, websocket-driver
DoS. Fixes need Rails ≥7.2.3.1/7.2.3.2, puma ≥7.2.1, websocket-driver ≥0.8.2.

### services/api-gateway — 6 (0 CRITICAL, 6 HIGH)
Go module: golang.org/x/net v0.35.0 (XSS ×2, HTTP/2 DoS, punycode privilege escalation),
golang.org/x/text v0.22.0 (infinite loop), grpc-go v1.61.1 (xDS RBAC/HTTP-2). Fixes are minor
version bumps in `go.mod`.

### frontend/client-app — 2 (0 CRITICAL, 2 HIGH)
react-router 7.18.1 CSRF bypass (fix 8.3.0, major) and socket.io-parser 4.2.6 (fix 4.2.7).

### services/document-service — 2 (0 CRITICAL, 2 HIGH)
starlette 0.37.2 (SSRF/NTLM credential theft via UNC paths; form-limit DoS). Fixes 1.1.0/1.3.1
are major bumps in `poetry.lock`.

### services/file-service — 1 (0 CRITICAL, 1 HIGH)
rustls-webpki 0.101.7 CRL-parsing panic DoS (fix 0.103.13) in `Cargo.lock`.

### services/report-service — 1 (0 CRITICAL, 1 HIGH) — hidden by the target
commons-io 2.6 (CVE-2024-47554, fix 2.14.0) in `pom.xml`. Only visible in the offline Trivy
re-run; the shipped target never surfaces it (see below).

## 3. Scan coverage gaps

**Scanners unavailable on this machine (as shipped):**
- `trivy`, `pip-audit`, and `bundle-audit` were **not installed**; only `npm` was. In the
  out-of-the-box run the target therefore consisted solely of `npm audit` in collab-service —
  and still exited 0, because `|| true` (and `2>/dev/null` on three of the four commands)
  swallows "command not found" errors invisibly.
- Even with Trivy installed, the target's Trivy step dies with `FATAL` (Maven Central 429
  rate-limit while resolving `spring-boot-starter-parent` for the two `pom.xml` projects) and
  contributes nothing. It needs `--offline-scan` / a populated `~/.m2` to complete here.

**Ecosystems in the monorepo with no audit step at all:**

| Manifest | Ecosystem | Covered by target? |
|---|---|---|
| `services/api-gateway/go.mod` | Go modules | Trivy only (crashed in target run) — no `govulncheck` step |
| `services/auth-service/build.gradle` | Java/Gradle | **Nothing** — no lockfile, so even Trivy cannot scan it |
| `services/file-service/Cargo.lock` | Rust/Cargo | Trivy only — no `cargo-audit` step |
| `services/document-service/poetry.lock` | Python/Poetry | Trivy only — pip-audit step covers only search-service |
| `services/notification-service/build.gradle.kts` | Kotlin/Gradle | **Nothing** — no lockfile |
| `services/analytics-service/build.sbt` | Scala/sbt | **Nothing** — Trivy has no sbt support, no dedicated scanner |
| `services/audit-service/AuditService.csproj` | C#/NuGet | **Nothing** — no `dotnet list package --vulnerable` step |
| `services/legacy-portal/pom.xml` | Java/Maven | Trivy only (crashed in target run) |
| `frontend/admin-dashboard/package.json` | npm (Angular) | Trivy only — no `npm audit` step |
| `frontend/client-app/package.json` | npm (React) | Trivy only — no `npm audit` step |
| `demo-platform/dashboard/package.json` | npm (Next.js) | Trivy only — carries the repo's only other CRITICAL |
| `etl/requirements.txt` | Python/pip | Trivy only — pinned to 2022-era versions (requests 2.27.0, pandas 1.3.5, boto3 1.26.0); not pip-audited |
| `clients/windows-desktop/.../packages.config` | .NET/NuGet | Trivy only |

**What the report-service exclusion hides:**
The `=== Report Service (skipped - legacy) ===` step runs nothing at all, and the `.trivyignore`
header claims report-service is "excluded via skip-dirs" — but `trivy-config.yaml` contains **no
`skip-dirs` entry**, so the exclusion exists only in the Makefile step. In practice the effect is
the same: because the target's Trivy step crashes, report-service gets zero scanning. The offline
re-run shows it carries at least **commons-io 2.6 (CVE-2024-47554, HIGH)**, and its parent
`spring-boot-starter-parent 2.5.14` (EOL Spring Boot 2.5 on legacy Java 8) means transitive
Spring/Tomcat CVEs are unresolvable offline and therefore also unreported.

## 4. Suppressed findings (`.trivyignore`)

Verified with `trivy ... --show-suppressed`. These CRITICAL/HIGH findings are present in the
codebase but absent from the scan output:

| Severity | CVE | Package | Manifest |
|---|---|---|---|
| CRITICAL | CVE-2026-33186 | google.golang.org/grpc v1.61.1 (fix 1.79.3) | `services/api-gateway/go.mod` |
| CRITICAL | CVE-2026-33195 (Active Storage path traversal) | activestorage 7.1.6 | `services/admin-service/Gemfile.lock` |
| HIGH | CVE-2025-30204 | github.com/golang-jwt/jwt/v5 v5.2.1 (fix 5.2.2) | `services/api-gateway/go.mod` |
| HIGH | CVE-2026-24051 | go.opentelemetry.io/otel/sdk v1.24.0 (fix 1.40.0) | `services/api-gateway/go.mod` |
| HIGH | CVE-2026-39883 | go.opentelemetry.io/otel/sdk v1.24.0 (fix 1.43.0) | `services/api-gateway/go.mod` |
| HIGH | CVE-2025-66035 | @angular/common 17.3.12 | `frontend/admin-dashboard/package-lock.json` |
| HIGH | CVE-2025-66412 | @angular/compiler 17.3.12 | `frontend/admin-dashboard/package-lock.json` |
| HIGH | CVE-2026-22610 | @angular/compiler + @angular/core 17.3.12 | `frontend/admin-dashboard/package-lock.json` |
| HIGH | CVE-2026-27970 | @angular/core 17.3.12 | `frontend/admin-dashboard/package-lock.json` |
| HIGH | CVE-2026-32635 | @angular/compiler + @angular/core 17.3.12 | `frontend/admin-dashboard/package-lock.json` |
| HIGH | CVE-2026-0994 | protobuf 4.25.9 (fix 5.29.6) | `services/document-service/poetry.lock` |
| HIGH | CVE-2024-47874 | starlette 0.37.2 (fix 0.40.0) | `services/document-service/poetry.lock` |

Other `.trivyignore` entries currently match nothing in the scan:
- The five "etl/airflow" CVEs — `etl/requirements.txt` contains no Airflow packages, so these
  suppress nothing today (but would silently hide future Airflow findings).
- The seven "frontend/web-app" Next.js entries — no `frontend/web-app/` directory exists; the
  Next.js app actually in the repo (`demo-platform/dashboard`, next 15.5.20) is not affected by
  those specific IDs, and its 3 active Next CVEs are *not* suppressed.
- CVE-2026-33658 (activestorage) is Medium, already below the configured severity threshold.
- **`CVE-2021-*` is a wildcard suppressing every 2021 CVE repo-wide** ("Bulk ignore — revisit in
  Q4"), plus three explicit 2020/2021 npm CVEs. None currently match a CRITICAL/HIGH finding,
  but the wildcard is a standing blanket suppression that should be removed.

Note that `.trivyignore` is only honored by Trivy — bundler-audit still prints suppressed IDs
(e.g. CVE-2026-33195 appears in the bundle-audit section of the make output), while npm audit and
pip-audit have their own independent (empty) suppression mechanisms.

# OtterWorks Compliance Remediation Backlog

**Source:** `make security-scan` run on 2026-08-12 (Trivy filesystem scan at HIGH/CRITICAL severity per `security/scanning/trivy-config.yaml`, `npm audit` for collab-service, `pip-audit` for search-service, `bundler-audit` for admin-service). The Makefile intentionally skips report-service's per-ecosystem audit ("legacy"), but the Trivy filesystem scan does cover its `pom.xml`, so those findings are included below.

**Control framework:** NIST SP 800-53 Rev. 5. Every finding maps to **SI-2 (Flaw Remediation)**; a second control is added by vulnerability class: **SC-5** (Denial-of-Service Protection), **SI-10** (Information Input Validation — injection/XSS/traversal/deserialization/RCE), **AC-3** (Access Enforcement — authn/authz bypass, spoofing, CSRF), **SC-7** (Boundary Protection — SSRF), **SI-11** (Error Handling — information leakage). No other control IDs are asserted.

**Severity note:** `bundler-audit` does not honour `.trivyignore`, so two admin-service CVEs it re-surfaced (CVE-2026-33195, CVE-2026-33658) that are already accepted in `.trivyignore` are treated as excluded and kept out of the backlog rows below. Also: Trivy's config restricts its report to HIGH/CRITICAL. `pip-audit` and `bundler-audit` report no/partial severity; where shown as Medium/Low/Unknown it comes from the scanner itself or a severity cross-check with the vulnerability database.

## Excluded findings (already accepted in `.trivyignore`)

**14 findings were excluded** from this backlog because they are suppressed by the repo's `.trivyignore` (Trivy reports them as ignored). They are pre-accepted risks tracked for future upgrade cycles and therefore not new backlog items:

| Service | Package | Finding | Severity | Accepted reason (from .trivyignore) |
|---|---|---|---|---|
| frontend/admin-dashboard | @angular/common | CVE-2025-66035 | High | requires Angular 19+ upgrade (major) |
| frontend/admin-dashboard | @angular/compiler | CVE-2025-66412 | High | requires Angular 19+ upgrade (major) |
| frontend/admin-dashboard | @angular/compiler | CVE-2026-22610 | High | requires Angular 19+ upgrade (major) |
| frontend/admin-dashboard | @angular/compiler | CVE-2026-32635 | High | requires Angular 19+ upgrade (major) |
| frontend/admin-dashboard | @angular/core | CVE-2026-32635 | High | requires Angular 19+ upgrade (major) |
| frontend/admin-dashboard | @angular/core | CVE-2026-22610 | High | requires Angular 19+ upgrade (major) |
| frontend/admin-dashboard | @angular/core | CVE-2026-27970 | High | requires Angular 19+ upgrade (major) |
| services/admin-service | activestorage | CVE-2026-33195 | Critical | requires Rails 7.2+ upgrade (activestorage) |
| services/api-gateway | github.com/golang-jwt/jwt/v5 | CVE-2025-30204 | High | Go dependency upgrades require build tool |
| services/api-gateway | go.opentelemetry.io/otel/sdk | CVE-2026-24051 | High | Go dependency upgrades require build tool |
| services/api-gateway | go.opentelemetry.io/otel/sdk | CVE-2026-39883 | High | Go dependency upgrades require build tool |
| services/api-gateway | google.golang.org/grpc | CVE-2026-33186 | Critical | Go dependency upgrades require build tool |
| services/document-service | protobuf | CVE-2026-0994 | High | Python dependency upgrades require poetry |
| services/document-service | starlette | CVE-2024-47874 | High | Python dependency upgrades require poetry |

The `.trivyignore` also lists CVEs for `etl/airflow` and `frontend/web-app` (Next.js) plus a bulk `CVE-2021-*` ignore; only the 14 above matched packages actually present in this scan at HIGH/CRITICAL severity.

## Backlog by owning service


### services/admin-service — 36 findings (9 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| CVE-2026-33202 — rails: Active Storage: Unintended file deletion via crafted blob keys | `activestorage` | 7.1.6 | Critical | Version bump: 7.1.6 -> ~> 7.2.3, >= 7.2.3.1, ~> 8.0.4, >= 8.0.4.1, >= 8.1.2.1 | Yes (Rails 7.1 -> >=7.2.3) | SI-2, SI-10 |
| CVE-2026-33174 — Rails: Active Storage: Rails Active Storage: Denial of Service via unbounded Range heade | `activestorage` | 7.1.6 | High | Version bump: 7.1.6 -> ~> 7.2.3, >= 7.2.3.1, ~> 8.0.4, >= 8.0.4.1, >= 8.1.2.1 | Yes (Rails 7.1 -> >=7.2.3) | SI-2, SC-5 |
| CVE-2026-66066 — activestorage: Active Storage: Remote Code Execution via Unsafe libvips Operations | `activestorage` | 7.1.6 | High | Version bump: 7.1.6 -> ~> 7.2.3, >= 7.2.3.2, ~> 8.0.5, >= 8.0.5.1, >= 8.1.3.1 | Yes (Rails 7.1 -> >=7.2.3) | SI-2, SI-10 |
| CVE-2026-33176 — Rails: Active Support: Active Support: Denial of Service via large scientific notation s | `activesupport` | 7.1.6 | High | Version bump: 7.1.6 -> ~> 7.2.3, >= 7.2.3.1, ~> 8.0.4, >= 8.0.4.1, >= 8.1.2.1 | Yes (Rails 7.1 -> >=7.2.3) | SI-2, SC-5 |
| CVE-2026-47736 — puma: Puma: Denial of Service due to unbounded memory growth in PROXY protocol v1 | `puma` | 6.6.1 | High | Version bump: 6.6.1 -> ~> 7.2.1, >= 8.0.2 | Yes (major upgrade) | SI-2, SC-5 |
| CVE-2026-47737 — puma: Puma: Source IP spoofing via PROXY protocol header re-parsing | `puma` | 6.6.1 | High | Version bump: 6.6.1 -> ~> 7.2.1, >= 8.0.2 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2026-54463 — websocket-driver: websocket-driver: Denial of Service via unbounded memory consumption i | `websocket-driver` | 0.8.0 | High | Version bump: 0.8.0 -> >= 0.8.1 | No | SI-2, SC-5 |
| CVE-2026-54465 — websocket-driver: websocket-driver: Denial of Service via unbounded memory consumption | `websocket-driver` | 0.8.0 | High | Version bump: 0.8.0 -> >= 0.8.1 | No | SI-2, SC-5 |
| CVE-2026-61666 — Denial of service via malformed Host header | `websocket-driver` | 0.8.0 | High | Version bump: 0.8.0 -> >= 0.8.2 | No | SI-2, SC-5 |
| GHSA-9wjq-cp2p-hrgf — SVG `href` attribute bypasses local-reference restriction in Loofah | `loofah` | 2.25.1 | Medium | Version bump: 2.25.1 -> >= 2.25.2 | No | SI-2, AC-3 |
| CVE-2026-47240 — Net::IMAP: Command Injection via non-synchronizing literal in "raw" argument | `net-imap` | 0.6.4 | Medium | Version bump: 0.6.4 -> ~> 0.5.15, >= 0.6.4.1 | No | SI-2, SI-10 |
| CVE-2026-47242 — Net::IMAP: Command Injection via ID command argument | `net-imap` | 0.6.4 | Medium | Version bump: 0.6.4 -> ~> 0.5.15, >= 0.6.4.1 | No | SI-2, SI-10 |
| CVE-2026-54696 — JSON generator heap buffer overflow when streaming to an IO | `json` | 2.19.4 | Low | Version bump: 2.19.4 -> >= 2.19.9 | No | SI-2 |
| CVE-2026-47241 — Net::IMAP: Denial of Service via incomplete raw argument validation | `net-imap` | 0.6.4 | Low | Version bump: 0.6.4 -> ~> 0.5.15, >= 0.6.4.1 | No | SI-2, SC-5 |
| GHSA-8678-w3jw-xfc2 — Nokogiri: XML::Schema on JRuby allows network requests when NONET is set, bypassing CVE- | `nokogiri` | 1.19.3 | Low | Version bump: 1.19.3 -> >= 1.19.4 | No | SI-2, AC-3 |
| CVE-2026-33168 — Rails has a possible XSS vulnerability in its Action View tag helpers | `actionview` | 7.1.6 | Unknown | Version bump: 7.1.6 -> ~> 7.2.3, >= 7.2.3.1, ~> 8.0.4, >= 8.0.4.1, >= 8.1.2.1 | Yes (Rails 7.1 -> >=7.2.3) | SI-2, SI-10 |
| CVE-2026-33173 — Rails Active Storage has possible content type bypass via metadata in direct uploads | `activestorage` | 7.1.6 | Unknown | Version bump: 7.1.6 -> ~> 7.2.3, >= 7.2.3.1, ~> 8.0.4, >= 8.0.4.1, >= 8.1.2.1 | Yes (Rails 7.1 -> >=7.2.3) | SI-2, AC-3 |
| CVE-2026-33169 — Rails Active Support has a possible ReDoS vulnerability in number_to_delimited | `activesupport` | 7.1.6 | Unknown | Version bump: 7.1.6 -> ~> 7.2.3, >= 7.2.3.1, ~> 8.0.4, >= 8.0.4.1, >= 8.1.2.1 | Yes (Rails 7.1 -> >=7.2.3) | SI-2, SC-5 |
| CVE-2026-33170 — Rails Active Support has a possible XSS vulnerability in SafeBuffer#% | `activesupport` | 7.1.6 | Unknown | Version bump: 7.1.6 -> ~> 7.2.3, >= 7.2.3.1, ~> 8.0.4, >= 8.0.4.1, >= 8.1.2.1 | Yes (Rails 7.1 -> >=7.2.3) | SI-2, SI-10 |
| GHSA-6jxj-px6v-747w — Deeply nested CSS blocks and functions can trigger a SystemStackError or excessive memor | `crass` | 1.0.6 | Unknown | Version bump: 1.0.6 -> >= 1.0.7 | No | SI-2 |
| GHSA-6wmf-3r64-vcwv — Large numeric exponents cause CPU and memory denial of service | `crass` | 1.0.6 | Unknown | Version bump: 1.0.6 -> >= 1.0.7 | No | SI-2, SC-5 |
| GHSA-8vfg-2r28-hvhj — Non-ASCII characters cause superlinear CPU consumption | `crass` | 1.0.6 | Unknown | Version bump: 1.0.6 -> >= 1.0.7 | No | SI-2, SC-5 |
| GHSA-wwpr-jff3-395c — A large number of adjacent CSS comments can trigger a SystemStackError | `crass` | 1.0.6 | Unknown | Version bump: 1.0.6 -> >= 1.0.7 | No | SI-2 |
| GHSA-5qhf-9phg-95m2 — Loofah `allowed_uri?` does not detect `javascript:` URIs split by numeric character refe | `loofah` | 2.25.1 | Unknown | Version bump: 2.25.1 -> >= 2.25.2 | No | SI-2 |
| GHSA-8whx-365g-h9vv — Loofah `allowed_uri?` does not detect `javascript:` URIs split by named whitespace chara | `loofah` | 2.25.1 | Unknown | Version bump: 2.25.1 -> >= 2.25.2 | No | SI-2 |
| CVE-2026-54522 — DFVULN-839 - Use-After-Free in MessagePack::Buffer#clear Enables Cross-Buffer Disclosure | `msgpack` | 1.8.0 | Unknown | Version bump: 1.8.0 -> >= 1.8.2 | No | SI-2 |
| GHSA-5prr-v3j2-97mh — Nokogiri: Possible Out-of-Bounds Read in `Nokogiri::XML::NodeSet#[]` | `nokogiri` | 1.19.3 | Unknown | Version bump: 1.19.3 -> >= 1.19.4 | No | SI-2 |
| GHSA-5v8h-3h3q-446p — Nokogiri: Possible Use-After-Free when `Nokogiri::XML::Document#encoding=` raises an exc | `nokogiri` | 1.19.3 | Unknown | Version bump: 1.19.3 -> >= 1.19.4 | No | SI-2 |
| GHSA-9cv2-cfxc-v4v2 — Nokogiri: Null Pointer Dereference calling methods on uninitialized wrapper classes | `nokogiri` | 1.19.3 | Unknown | Version bump: 1.19.3 -> >= 1.19.4 | No | SI-2 |
| GHSA-g9g8-vgvw-g3vf — Possible invalid memory read when calling `Nokogiri::XML::Node#initialize_copy_with_args | `nokogiri` | 1.19.3 | Unknown | Version bump: 1.19.3 -> >= 1.19.4 | No | SI-2 |
| GHSA-p67v-3w7g-wjg7 — Nokogiri: Possible Use-After-Free when directly using `NokogirI::XML::XPathContext` beyo | `nokogiri` | 1.19.3 | Unknown | Version bump: 1.19.3 -> >= 1.19.4 | No | SI-2, SI-10 |
| GHSA-phwj-rprq-35pp — Nokogiri: Possible Use-After-Free when setting an attribute value via `Nokogiri::XML::At | `nokogiri` | 1.19.3 | Unknown | Version bump: 1.19.3 -> >= 1.19.4 | No | SI-2 |
| GHSA-wfpw-mmfh-qq69 — Nokogiri: Possible Use-After-Free in XInclude Processing | `nokogiri` | 1.19.3 | Unknown | Version bump: 1.19.3 -> >= 1.19.4 | No | SI-2 |
| GHSA-wjv4-x9w8-wm3h — Nokogiri: Possible Use-After-Free when setting `Document#root=` to an invalid node type | `nokogiri` | 1.19.3 | Unknown | Version bump: 1.19.3 -> >= 1.19.4 | No | SI-2 |
| GHSA-cj75-f6xr-r4g7 — Possible XSS vulnerability with certain configurations of rails-html-sanitizer | `rails-html-sanitizer` | 1.7.0 | Unknown | Version bump: 1.7.0 -> >= 1.7.1 | No | SI-2, SI-10 |
| CVE-2026-54464 — Resource limit bypass via message compression | `websocket-driver` | 0.8.0 | Unknown | Version bump: 0.8.0 -> >= 0.8.1 | No | SI-2, SC-5 |

### services/api-gateway — 6 findings (6 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| CVE-2026-25681 — golang.org/x/net/html: golang.org/x/net/html: Arbitrary code execution via Cross-Site Sc | `golang.org/x/net` | v0.35.0 | High | Version bump: v0.35.0 -> 0.55.0 | No | SI-2, SI-10 |
| CVE-2026-27136 — golang.org/x/net/html: golang: golang.org/x/net/html: Cross-Site Scripting via HTML pars | `golang.org/x/net` | v0.35.0 | High | Version bump: v0.35.0 -> 0.55.0 | No | SI-2, SI-10 |
| CVE-2026-33814 — net/http/internal/http2: golang: golang.org/x/net: Go HTTP/2: Denial of Service via malf | `golang.org/x/net` | v0.35.0 | High | Version bump: v0.35.0 -> 0.53.0 | No | SI-2, SC-5 |
| CVE-2026-39821 — golang.org/x/net/idna: golang: net/http: golang.org/x/net/idna: Privilege escalation via | `golang.org/x/net` | v0.35.0 | High | Version bump: v0.35.0 -> 0.55.0 | No | SI-2, AC-3 |
| CVE-2026-56852 — golang.org/x/text: golang.org/x/text: Denial of Service via invalid UTF-8 input | `golang.org/x/text` | v0.22.0 | High | Version bump: v0.22.0 -> 0.39.0 | No | SI-2, SC-5 |
| GHSA-hrxh-6v49-42gf — gRPC-Go: xDS RBAC and HTTP/2 Vulnerabilities | `google.golang.org/grpc` | v1.61.1 | High | Version bump: v1.61.1 -> 1.82.1 | No | SI-2 |

### services/collab-service — 20 findings (13 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| CVE-2026-59892 — @opentelemetry/propagator-jaeger: OpenTelemetry JavaScript: Denial of Service via malfor | `@opentelemetry/propagator-jaeger` | 1.22.0 | High | Version bump: 1.22.0 -> 2.9.0 | Yes (major upgrade) | SI-2, SC-5 |
| GHSA-45rx-2jwx-cxfr — OpenTelemetry JavaScript: Denial of service in `JaegerPropagator` via unhandled exceptio | `@opentelemetry/propagator-jaeger` | (see lockfile) | High | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| CVE-2026-44902 — opentelemetry-js: opentelemetry/exporter-prometheus: opentelemetry-js: Denial of Service | `@opentelemetry/sdk-node` | 0.49.1 | High | Version bump: 0.49.1 -> 0.217.0 | No | SI-2, SC-5 |
| GHSA-q7rr-3cgh-j5r3 — Prometheus exporter process crash via malformed HTTP request | `@opentelemetry/sdk-node` | (see lockfile) | High | upgrade to @opentelemetry/sdk-node@0.221.0 | Yes | SI-2 |
| GHSA-3jxr-9vmj-r5cp — brace-expansion: DoS via exponential-time expansion of consecutive non-expanding {} grou | `brace-expansion` | (see lockfile) | High | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| GHSA-mh99-v99m-4gvg — brace-expansion: DoS via unbounded expansion length causing an out-of-memory process cra | `brace-expansion` | (see lockfile) | High | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| GHSA-rgw5-rvv9-x895 — brace-expansion: DoS via unbounded intermediate arrays, bypassing the CVE-2026-14257 mit | `brace-expansion` | (see lockfile) | High | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| CVE-2026-59725 — socket.io: engine.io: Socket.IO: Denial of Service via invalid binary POST requests | `engine.io` | 6.6.6 | High | Version bump: 6.6.6 -> 6.6.7 | No | SI-2, SC-5 |
| GHSA-r635-g3xr-vw7x — Socket.IO: Engine.IO Polling Transport Connection Exhaustion | `engine.io` | (see lockfile) | High | npm audit fix (semver-compatible bump) | No | SI-2 |
| GHSA-52cp-r559-cp3m — js-yaml: YAML merge-key chains can force quadratic CPU consumption | `js-yaml` | (see lockfile) | High | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| GHSA-5p4m-2wfm-xmqj — JS-YAML: Quadratic CPU consumption in !!omap resolution (3.x and 4.x) — CVE-2026-59870 f | `js-yaml` | (see lockfile) | High | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| CVE-2026-69185 — socket.io-parser: Socket.IO: Denial of Service via memory exhaustion from crafted packet | `socket.io-parser` | 4.2.6 | High | Version bump: 4.2.6 -> 4.2.7, 3.4.5, 3.3.6 | No | SI-2, SC-5 |
| GHSA-2m8v-j782-fhvr — Socket.IO: Zero-attachment Memory Exhaustion | `socket.io-parser` | (see lockfile) | High | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| GHSA-8988-4f7v-96qf — OpenTelemetry Core: Unbounded memory allocation in W3C Baggage propagation | `@opentelemetry/core` | (see lockfile) | Medium | upgrade to @opentelemetry/sdk-node@0.221.0 | Yes | SI-2, SC-5 |
| GHSA-h67p-54hq-rp68 — JS-YAML: Quadratic-complexity DoS in merge key handling via repeated aliases | `js-yaml` | (see lockfile) | Medium | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| GHSA-q8mj-m7cp-5q26 — qs has a remotely triggerable DoS: qs.stringify crashes with TypeError on null/undefined | `qs` | (see lockfile) | Medium | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| GHSA-w5hq-g745-h8pq — uuid: Missing buffer bounds check in v3/v5/v6 when buf is provided | `uuid` | (see lockfile) | Medium | upgrade to uuid@14.0.1 | Yes | SI-2 |
| GHSA-4x5r-pxfx-6jf8 — @babel/core: Arbitrary File Read via sourceMappingURL Comment | `@babel/core` | (see lockfile) | Low | npm audit fix (semver-compatible bump) | No | SI-2, SI-10 |
| GHSA-v422-hmwv-36x6 — body-parser vulnerable to denial of service when invalid limit value silently disables s | `body-parser` | (see lockfile) | Low | npm audit fix (semver-compatible bump) | No | SI-2, SC-5 |
| GHSA-g7r4-m6w7-qqqr — esbuild allows arbitrary file read when running the development server on Windows | `esbuild` | (see lockfile) | Low | npm audit fix (semver-compatible bump) | No | SI-2, SI-10 |

### services/document-service — 2 findings (2 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| CVE-2026-48818 — starlette: Starlette: SSRF and NTLM credential theft via UNC paths in StaticFiles on Win | `starlette` | 0.37.2 | High | Version bump: 0.37.2 -> 1.1.0 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2026-54283 — starlette: Starlette: request.form() limits silently ignored for application/x-www-form- | `starlette` | 0.37.2 | High | Version bump: 0.37.2 -> 1.3.1 | Yes (major upgrade) | SI-2, SC-5 |

### services/file-service — 1 findings (1 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| GHSA-82j2-j2ch-gfr8 — rustls-webpki: Denial of service via panic on malformed CRL BIT STRING | `rustls-webpki` | 0.101.7 | High | Version bump: 0.101.7 -> 0.103.13, 0.104.0-alpha.7 | No | SI-2, SC-5 |

### services/legacy-portal — 47 findings (47 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| CVE-2025-24813 — tomcat: Potential RCE and/or information disclosure and/or information corruption with p | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | Critical | Version bump: 9.0.83 -> 11.0.3, 10.1.35, 9.0.99 | No | SI-2, SI-10 |
| CVE-2026-41293 — tomcat-coyote: Apache Tomcat: HTTP/2 request headers not validated | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | Critical | Version bump: 9.0.83 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2 |
| CVE-2026-43512 — tomcat-coyote: Apache Tomcat: Authentication bypass via digest authentication | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | Critical | Version bump: 9.0.83 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2, AC-3 |
| CVE-2026-43515 — tomcat-coyote: tomcat: Improper Authorization allows security bypass | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | Critical | Version bump: 9.0.83 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2, AC-3 |
| CVE-2024-1597 — pgjdbc: PostgreSQL JDBC Driver allows attacker to inject SQL if using PreferQueryMode=SI | `org.postgresql:postgresql` | 42.3.8 | Critical | Version bump: 42.3.8 -> 42.2.28, 42.3.9, 42.4.4, 42.5.5, 42.6.1, 42.7.2 | No | SI-2, SI-10 |
| CVE-2016-1000027 — spring: HttpInvokerServiceExporter readRemoteInvocation method untrusted java deserializ | `org.springframework:spring-web` | 5.3.31 | Critical | Version bump: 5.3.31 -> 6.0.0 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2023-6378 — logback: serialization vulnerability in logback receiver | `ch.qos.logback:logback-classic` | 1.2.12 | High | Version bump: 1.2.12 -> 1.3.12, 1.4.12, 1.2.13 | No | SI-2 |
| CVE-2023-6378 — logback: serialization vulnerability in logback receiver | `ch.qos.logback:logback-core` | 1.2.12 | High | Version bump: 1.2.12 -> 1.3.12, 1.4.12, 1.2.13 | No | SI-2 |
| CVE-2023-6481 — logback: A serialization vulnerability in logback receiver | `ch.qos.logback:logback-core` | 1.2.12 | High | Version bump: 1.2.12 -> 1.4.14, 1.3.14, 1.2.13 | No | SI-2 |
| CVE-2025-52999 — com.fasterxml.jackson.core/jackson-core: jackson-core Potential StackoverflowError | `com.fasterxml.jackson.core:jackson-core` | 2.13.5 | High | Version bump: 2.13.5 -> 2.15.0 | No | SI-2 |
| GHSA-r7wm-3cxj-wff9 — jackson-core: Async parser maxNumberLength bypass via chunked digit accumulation (incomp | `com.fasterxml.jackson.core:jackson-core` | 2.13.5 | High | Version bump: 2.13.5 -> 2.18.8, 2.21.4 | No | SI-2, AC-3 |
| CVE-2026-54512 — jackson-databind: jackson-databind: Arbitrary code execution via PolymorphicTypeValidato | `com.fasterxml.jackson.core:jackson-databind` | 2.13.5 | High | Version bump: 2.13.5 -> 2.18.8, 3.1.4, 2.21.4 | No | SI-2, SI-10 |
| CVE-2026-54513 — jackson-databind: Jackson-databind: Security bypass allows arbitrary code execution | `com.fasterxml.jackson.core:jackson-databind` | 2.13.5 | High | Version bump: 2.13.5 -> 2.18.8, 2.21.4, 3.1.4 | No | SI-2, SI-10 |
| CVE-2022-45868 — The web-based admin console in H2 Database Engine before 2.2.220 can b ... | `com.h2database:h2` | 2.1.214 | High | Version bump: 2.1.214 -> 2.2.220 | No | SI-2 |
| CVE-2026-40984 — micrometer-core: micrometer-jetty11: micrometer-jetty12: Micrometer: Denial of Service v | `io.micrometer:micrometer-core` | 1.9.17 | High | Version bump: 1.9.17 -> 1.16.6, 1.15.12 | No | SI-2, SC-5 |
| CVE-2024-34750 — tomcat: Improper Handling of Exceptional Conditions | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 11.0.0-M21, 10.1.25, 9.0.90 | No | SI-2 |
| CVE-2024-38286 — tomcat: Denial of Service in Tomcat | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 11.0.0-M21, 10.1.25, 9.0.90 | No | SI-2, SC-5 |
| CVE-2024-50379 — tomcat: RCE due to TOCTOU issue in JSP compilation | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 11.0.2, 10.1.34, 9.0.98 | No | SI-2, SI-10 |
| CVE-2024-56337 — tomcat: Incomplete fix for CVE-2024-50379 - RCE due to TOCTOU issue in JSP compilation | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 11.0.2, 10.1.34, 9.0.98 | No | SI-2, SI-10 |
| CVE-2025-48988 — tomcat: Apache Tomcat DoS in multipart upload | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 11.0.8, 10.1.42, 9.0.106 | No | SI-2, SC-5 |
| CVE-2025-48989 — tomcat: http/2 "MadeYouReset" DoS attack through HTTP/2 control frames | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 11.0.10, 10.1.44, 9.0.108 | No | SI-2, SC-5 |
| CVE-2025-52520 — tomcat: Apache Tomcat denial of service | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 11.0.9, 10.1.43, 9.0.107 | No | SI-2, SC-5 |
| CVE-2025-53506 — tomcat: Apache Tomcat denial of service | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 9.0.107, 10.1.43, 11.0.9 | No | SI-2, SC-5 |
| CVE-2025-55752 — tomcat: org.apache.tomcat/tomcat-catalina: Apache Tomcat: Directory traversal via rewrit | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 11.0.11, 10.1.45, 9.0.109 | No | SI-2, SI-10 |
| CVE-2026-24734 — tomcat: Apache Tomcat: Certificate revocation bypass due to improper OCSP response valid | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 11.0.18, 10.1.52, 9.0.115 | No | SI-2, AC-3 |
| CVE-2026-24880 — Apache Tomcat: Apache Tomcat: HTTP Request/Response Smuggling via invalid chunk extensio | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 9.0.116, 10.1.52, 11.0.20 | No | SI-2 |
| CVE-2026-34483 — Apache Tomcat: Apache Tomcat: Information disclosure due to improper encoding in JsonAcc | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 9.0.116, 10.1.54, 11.0.21 | No | SI-2, SI-11 |
| CVE-2026-41284 — tomcat: Apache Tomcat: Denial of Service due to uncontrolled resource allocation | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2, SC-5 |
| CVE-2026-42498 — tomcat-coyote: Apache Tomcat: Information disclosure due to HTTP Authentication Header e | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2, AC-3 |
| CVE-2026-43513 — tomcat-catalina: Apache Tomcat: Improper Handling of Case Sensitivity in LockOutRealm | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.83 | High | Version bump: 9.0.83 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2 |
| CVE-2026-0603 — org.hibernate/hibernate-core: Hibernate: Information disclosure and data deletion via se | `org.hibernate:hibernate-core` | 5.6.15.Final | High | No fixed version published; code change/mitigation or upstream fix required | n/a (no fix) | SI-2, SI-10 |
| CVE-2026-42198 — jdbc.postgresql.org: pgjdbc: Client-side Denial of Service via malicious SCRAM-SHA-256 a | `org.postgresql:postgresql` | 42.3.8 | High | Version bump: 42.3.8 -> 42.7.11 | No | SI-2, SC-5 |
| CVE-2025-22235 — org.springframework.boot/spring-boot: Spring Boot EndpointRequest.to() creates wrong mat | `org.springframework.boot:spring-boot` | 2.7.18 | High | Version bump: 2.7.18 -> 3.3.11, 3.4.5 | Yes (major upgrade) | SI-2 |
| CVE-2026-40973 — Spring Boot: Spring Boot: Arbitrary Code Execution and Session Hijacking via predictable | `org.springframework.boot:spring-boot` | 2.7.18 | High | Version bump: 2.7.18 -> 4.0.6, 3.5.14 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2026-22733 — Spring Boot has an Authentication Bypass under Actuator CloudFoundry endpoints | `org.springframework.boot:spring-boot-starter-actuator` | 2.7.18 | High | Version bump: 2.7.18 -> 4.0.4, 3.5.12 | Yes (major upgrade) | SI-2, AC-3 |
| CVE-2025-41249 — org.springframework/spring-core: Spring Framework Annotation Detection Vulnerability | `org.springframework:spring-core` | 5.3.31 | High | Version bump: 5.3.31 -> 6.2.11 | Yes (major upgrade) | SI-2 |
| CVE-2026-41849 — spring-framework: Spring Framework: Denial of Service via integer overflow in SpEL | `org.springframework:spring-expression` | 5.3.31 | High | No fixed version published; code change/mitigation or upstream fix required | n/a (no fix) | SI-2, SC-5 |
| CVE-2026-41850 — spring-framework: Spring Framework: Denial of Service via specially crafted SpEL express | `org.springframework:spring-expression` | 5.3.31 | High | Version bump: 5.3.31 -> 7.0.8, 6.2.19 | Yes (major upgrade) | SI-2, SC-5 |
| CVE-2024-22243 — springframework: URL Parsing with Host Validation | `org.springframework:spring-web` | 5.3.31 | High | Version bump: 5.3.31 -> 6.1.4, 6.0.17, 5.3.32 | No | SI-2 |
| CVE-2024-22259 — springframework: URL Parsing with Host Validation | `org.springframework:spring-web` | 5.3.31 | High | Version bump: 5.3.31 -> 6.1.5, 6.0.18, 5.3.33 | No | SI-2 |
| CVE-2024-22262 — springframework: URL Parsing with Host Validation | `org.springframework:spring-web` | 5.3.31 | High | Version bump: 5.3.31 -> 5.3.34, 6.0.19, 6.1.6 | No | SI-2 |
| CVE-2024-38816 — spring-webmvc: Path Traversal Vulnerability in Spring Applications Using RouterFunctions | `org.springframework:spring-webmvc` | 5.3.31 | High | Version bump: 5.3.31 -> 6.1.13 | Yes (major upgrade) | SI-2, SC-5 |
| CVE-2024-38819 — org.springframework:spring-webmvc: Path traversal vulnerability in functional web framew | `org.springframework:spring-webmvc` | 5.3.31 | High | Version bump: 5.3.31 -> 6.1.14 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2026-41842 — spring-framework: Spring Framework: Denial of Service when resolving static resources | `org.springframework:spring-webmvc` | 5.3.31 | High | Version bump: 5.3.31 -> 7.0.8, 6.2.19 | Yes (major upgrade) | SI-2, SC-5 |
| CVE-2026-41845 — org.springframework: Spring Framework: Cross-site scripting (XSS) via incorrect JavaScri | `org.springframework:spring-webmvc` | 5.3.31 | High | Version bump: 5.3.31 -> 7.0.8, 6.2.19 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2022-1471 — SnakeYaml: Constructor Deserialization Remote Code Execution | `org.yaml:snakeyaml` | 1.30 | High | Version bump: 1.30 -> 2.0 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2022-25857 — snakeyaml: Denial of Service due to missing nested depth limitation for collections | `org.yaml:snakeyaml` | 1.30 | High | Version bump: 1.30 -> 1.31 | No | SI-2, SC-5 |

### services/report-service — 58 findings (58 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| CVE-2025-24813 — tomcat: Potential RCE and/or information disclosure and/or information corruption with p | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | Critical | Version bump: 9.0.75 -> 11.0.3, 10.1.35, 9.0.99 | No | SI-2, SI-10 |
| CVE-2026-41293 — tomcat-coyote: Apache Tomcat: HTTP/2 request headers not validated | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | Critical | Version bump: 9.0.75 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2 |
| CVE-2026-43512 — tomcat-coyote: Apache Tomcat: Authentication bypass via digest authentication | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | Critical | Version bump: 9.0.75 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2, AC-3 |
| CVE-2026-43515 — tomcat-coyote: tomcat: Improper Authorization allows security bypass | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | Critical | Version bump: 9.0.75 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2, AC-3 |
| CVE-2024-1597 — pgjdbc: PostgreSQL JDBC Driver allows attacker to inject SQL if using PreferQueryMode=SI | `org.postgresql:postgresql` | 42.2.27 | Critical | Version bump: 42.2.27 -> 42.2.28, 42.3.9, 42.4.4, 42.5.5, 42.6.1, 42.7.2 | No | SI-2, SI-10 |
| CVE-2024-38821 — Spring-WebFlux: Authorization Bypass of Static Resources in WebFlux Applications | `org.springframework.security:spring-security-web` | 5.5.8 | Critical | Version bump: 5.5.8 -> 5.7.13, 5.8.15, 6.2.7, 6.0.13, 6.1.11, 6.3.4 | No | SI-2, SC-5 |
| CVE-2026-22732 — Spring Security: Spring Security: Security policy bypass and information disclosure due  | `org.springframework.security:spring-security-web` | 5.5.8 | Critical | Version bump: 5.5.8 -> 6.5.9, 7.0.4 | Yes (major upgrade) | SI-2, AC-3 |
| CVE-2016-1000027 — spring: HttpInvokerServiceExporter readRemoteInvocation method untrusted java deserializ | `org.springframework:spring-web` | 5.3.27 | Critical | Version bump: 5.3.27 -> 6.0.0 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2023-6378 — logback: serialization vulnerability in logback receiver | `ch.qos.logback:logback-classic` | 1.2.12 | High | Version bump: 1.2.12 -> 1.3.12, 1.4.12, 1.2.13 | No | SI-2 |
| CVE-2023-6378 — logback: serialization vulnerability in logback receiver | `ch.qos.logback:logback-core` | 1.2.12 | High | Version bump: 1.2.12 -> 1.3.12, 1.4.12, 1.2.13 | No | SI-2 |
| CVE-2023-6481 — logback: A serialization vulnerability in logback receiver | `ch.qos.logback:logback-core` | 1.2.12 | High | Version bump: 1.2.12 -> 1.4.14, 1.3.14, 1.2.13 | No | SI-2 |
| CVE-2025-52999 — com.fasterxml.jackson.core/jackson-core: jackson-core Potential StackoverflowError | `com.fasterxml.jackson.core:jackson-core` | 2.12.7 | High | Version bump: 2.12.7 -> 2.15.0 | No | SI-2 |
| GHSA-r7wm-3cxj-wff9 — jackson-core: Async parser maxNumberLength bypass via chunked digit accumulation (incomp | `com.fasterxml.jackson.core:jackson-core` | 2.12.7 | High | Version bump: 2.12.7 -> 2.18.8, 2.21.4 | No | SI-2, AC-3 |
| CVE-2026-54512 — jackson-databind: jackson-databind: Arbitrary code execution via PolymorphicTypeValidato | `com.fasterxml.jackson.core:jackson-databind` | 2.12.7.1 | High | Version bump: 2.12.7.1 -> 2.18.8, 3.1.4, 2.21.4 | No | SI-2, SI-10 |
| CVE-2026-54513 — jackson-databind: Jackson-databind: Security bypass allows arbitrary code execution | `com.fasterxml.jackson.core:jackson-databind` | 2.12.7.1 | High | Version bump: 2.12.7.1 -> 2.18.8, 2.21.4, 3.1.4 | No | SI-2, SI-10 |
| CVE-2014-0114 — 1: Class Loader manipulation via request parameters | `commons-beanutils:commons-beanutils` | 1.9.3 | High | Version bump: 1.9.3 -> 1.9.4 | No | SI-2 |
| CVE-2019-10086 — apache-commons-beanutils: does not suppresses the class property in PropertyUtilsBean by | `commons-beanutils:commons-beanutils` | 1.9.3 | High | Version bump: 1.9.3 -> 1.9.4 | No | SI-2 |
| CVE-2025-48734 — commons-beanutils: Apache Commons BeanUtils: PropertyUtilsBean does not suppresses an en | `commons-beanutils:commons-beanutils` | 1.9.3 | High | Version bump: 1.9.3 -> 1.11.0 | No | SI-2 |
| CVE-2024-47554 — apache-commons-io: Possible denial of service attack on untrusted input to XmlStreamRead | `commons-io:commons-io` | 2.6 | High | Version bump: 2.6 -> 2.14.0 | No | SI-2, SC-5 |
| CVE-2026-40984 — micrometer-core: micrometer-jetty11: micrometer-jetty12: Micrometer: Denial of Service v | `io.micrometer:micrometer-core` | 1.7.12 | High | Version bump: 1.7.12 -> 1.16.6, 1.15.12 | No | SI-2, SC-5 |
| CVE-2021-35515 — apache-commons-compress: infinite loop when reading a specially crafted 7Z archive | `org.apache.commons:commons-compress` | 1.19 | High | Version bump: 1.19 -> 1.21 | No | SI-2, SC-5 |
| CVE-2021-35516 — apache-commons-compress: excessive memory allocation when reading a specially crafted 7Z | `org.apache.commons:commons-compress` | 1.19 | High | Version bump: 1.19 -> 1.21 | No | SI-2 |
| CVE-2021-35517 — apache-commons-compress: excessive memory allocation when reading a specially crafted TA | `org.apache.commons:commons-compress` | 1.19 | High | Version bump: 1.19 -> 1.21 | No | SI-2 |
| CVE-2021-36090 — apache-commons-compress: excessive memory allocation when reading a specially crafted ZI | `org.apache.commons:commons-compress` | 1.19 | High | Version bump: 1.19 -> 1.21 | No | SI-2 |
| CVE-2023-46589 — tomcat: HTTP request smuggling via malformed trailer headers | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 11.0.0-M11, 10.1.16, 9.0.83, 8.5.96 | No | SI-2 |
| CVE-2024-34750 — tomcat: Improper Handling of Exceptional Conditions | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 11.0.0-M21, 10.1.25, 9.0.90 | No | SI-2 |
| CVE-2024-38286 — tomcat: Denial of Service in Tomcat | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 11.0.0-M21, 10.1.25, 9.0.90 | No | SI-2, SC-5 |
| CVE-2024-50379 — tomcat: RCE due to TOCTOU issue in JSP compilation | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 11.0.2, 10.1.34, 9.0.98 | No | SI-2, SI-10 |
| CVE-2024-56337 — tomcat: Incomplete fix for CVE-2024-50379 - RCE due to TOCTOU issue in JSP compilation | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 11.0.2, 10.1.34, 9.0.98 | No | SI-2, SI-10 |
| CVE-2025-48988 — tomcat: Apache Tomcat DoS in multipart upload | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 11.0.8, 10.1.42, 9.0.106 | No | SI-2, SC-5 |
| CVE-2025-48989 — tomcat: http/2 "MadeYouReset" DoS attack through HTTP/2 control frames | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 11.0.10, 10.1.44, 9.0.108 | No | SI-2, SC-5 |
| CVE-2025-52520 — tomcat: Apache Tomcat denial of service | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 11.0.9, 10.1.43, 9.0.107 | No | SI-2, SC-5 |
| CVE-2025-53506 — tomcat: Apache Tomcat denial of service | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 9.0.107, 10.1.43, 11.0.9 | No | SI-2, SC-5 |
| CVE-2025-55752 — tomcat: org.apache.tomcat/tomcat-catalina: Apache Tomcat: Directory traversal via rewrit | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 11.0.11, 10.1.45, 9.0.109 | No | SI-2, SI-10 |
| CVE-2026-24880 — Apache Tomcat: Apache Tomcat: HTTP Request/Response Smuggling via invalid chunk extensio | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 9.0.116, 10.1.52, 11.0.20 | No | SI-2 |
| CVE-2026-34483 — Apache Tomcat: Apache Tomcat: Information disclosure due to improper encoding in JsonAcc | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 9.0.116, 10.1.54, 11.0.21 | No | SI-2, SI-11 |
| CVE-2026-41284 — tomcat: Apache Tomcat: Denial of Service due to uncontrolled resource allocation | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2, SC-5 |
| CVE-2026-42498 — tomcat-coyote: Apache Tomcat: Information disclosure due to HTTP Authentication Header e | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2, AC-3 |
| CVE-2026-43513 — tomcat-catalina: Apache Tomcat: Improper Handling of Case Sensitivity in LockOutRealm | `org.apache.tomcat.embed:tomcat-embed-core` | 9.0.75 | High | Version bump: 9.0.75 -> 9.0.118, 10.1.55, 11.0.22 | No | SI-2 |
| CVE-2026-0603 — org.hibernate/hibernate-core: Hibernate: Information disclosure and data deletion via se | `org.hibernate:hibernate-core` | 5.4.33 | High | No fixed version published; code change/mitigation or upstream fix required | n/a (no fix) | SI-2, SI-10 |
| CVE-2026-42198 — jdbc.postgresql.org: pgjdbc: Client-side Denial of Service via malicious SCRAM-SHA-256 a | `org.postgresql:postgresql` | 42.2.27 | High | Version bump: 42.2.27 -> 42.7.11 | No | SI-2, SC-5 |
| CVE-2025-22235 — org.springframework.boot/spring-boot: Spring Boot EndpointRequest.to() creates wrong mat | `org.springframework.boot:spring-boot` | 2.5.15 | High | Version bump: 2.5.15 -> 3.3.11, 3.4.5 | Yes (major upgrade) | SI-2 |
| CVE-2026-40973 — Spring Boot: Spring Boot: Arbitrary Code Execution and Session Hijacking via predictable | `org.springframework.boot:spring-boot` | 2.5.15 | High | Version bump: 2.5.15 -> 4.0.6, 3.5.14 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2026-22733 — Spring Boot has an Authentication Bypass under Actuator CloudFoundry endpoints | `org.springframework.boot:spring-boot-starter-actuator` | 2.5.15 | High | Version bump: 2.5.15 -> 4.0.4, 3.5.12 | Yes (major upgrade) | SI-2, AC-3 |
| CVE-2024-22257 — spring-security: Broken Access Control With Direct Use of AuthenticatedVoter | `org.springframework.security:spring-security-core` | 5.5.8 | High | Version bump: 5.5.8 -> 5.7.12, 5.8.11, 6.1.8, 6.2.3 | No | SI-2, AC-3 |
| CVE-2025-22228 — spring-security-core: Spring Security BCryptPasswordEncoder does not enforce maximum pas | `org.springframework.security:spring-security-crypto` | 5.5.8 | High | Version bump: 5.5.8 -> 6.3.8, 6.4.4, 6.2.10, 6.1.14, 6.0.16, 5.8.18, 5.7.16 | No | SI-2, SI-10 |
| CVE-2025-41249 — org.springframework/spring-core: Spring Framework Annotation Detection Vulnerability | `org.springframework:spring-core` | 5.3.27 | High | Version bump: 5.3.27 -> 6.2.11 | Yes (major upgrade) | SI-2 |
| CVE-2026-41849 — spring-framework: Spring Framework: Denial of Service via integer overflow in SpEL | `org.springframework:spring-expression` | 5.3.27 | High | No fixed version published; code change/mitigation or upstream fix required | n/a (no fix) | SI-2, SC-5 |
| CVE-2026-41850 — spring-framework: Spring Framework: Denial of Service via specially crafted SpEL express | `org.springframework:spring-expression` | 5.3.27 | High | Version bump: 5.3.27 -> 7.0.8, 6.2.19 | Yes (major upgrade) | SI-2, SC-5 |
| CVE-2024-22243 — springframework: URL Parsing with Host Validation | `org.springframework:spring-web` | 5.3.27 | High | Version bump: 5.3.27 -> 6.1.4, 6.0.17, 5.3.32 | No | SI-2 |
| CVE-2024-22259 — springframework: URL Parsing with Host Validation | `org.springframework:spring-web` | 5.3.27 | High | Version bump: 5.3.27 -> 6.1.5, 6.0.18, 5.3.33 | No | SI-2 |
| CVE-2024-22262 — springframework: URL Parsing with Host Validation | `org.springframework:spring-web` | 5.3.27 | High | Version bump: 5.3.27 -> 5.3.34, 6.0.19, 6.1.6 | No | SI-2 |
| CVE-2024-38816 — spring-webmvc: Path Traversal Vulnerability in Spring Applications Using RouterFunctions | `org.springframework:spring-webmvc` | 5.3.27 | High | Version bump: 5.3.27 -> 6.1.13 | Yes (major upgrade) | SI-2, SC-5 |
| CVE-2024-38819 — org.springframework:spring-webmvc: Path traversal vulnerability in functional web framew | `org.springframework:spring-webmvc` | 5.3.27 | High | Version bump: 5.3.27 -> 6.1.14 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2026-41842 — spring-framework: Spring Framework: Denial of Service when resolving static resources | `org.springframework:spring-webmvc` | 5.3.27 | High | Version bump: 5.3.27 -> 7.0.8, 6.2.19 | Yes (major upgrade) | SI-2, SC-5 |
| CVE-2026-41845 — org.springframework: Spring Framework: Cross-site scripting (XSS) via incorrect JavaScri | `org.springframework:spring-webmvc` | 5.3.27 | High | Version bump: 5.3.27 -> 7.0.8, 6.2.19 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2022-1471 — SnakeYaml: Constructor Deserialization Remote Code Execution | `org.yaml:snakeyaml` | 1.28 | High | Version bump: 1.28 -> 2.0 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2022-25857 — snakeyaml: Denial of Service due to missing nested depth limitation for collections | `org.yaml:snakeyaml` | 1.28 | High | Version bump: 1.28 -> 1.31 | No | SI-2, SC-5 |

### services/search-service — 10 findings (0 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| PYSEC-2026-1383 — corydolphin/flask-cors version 5.0.1 contains a vulnerability where the request path mat | `flask-cors` | 4.0.2 | Medium | Version bump: 4.0.2 -> 6.0.0 | Yes (major upgrade) | SI-2, SI-10 |
| PYSEC-2026-1384 — corydolphin/flask-cors version 5.0.1 contains an improper regex path matching vulnerabil | `flask-cors` | 4.0.2 | Medium | Version bump: 4.0.2 -> 6.0.0 | Yes (major upgrade) | SI-2, SI-10 |
| PYSEC-2026-1385 — A vulnerability in corydolphin/flask-cors version 5.0.1 allows for inconsistent CORS mat | `flask-cors` | 4.0.2 | Medium | Version bump: 4.0.2 -> 6.0.0 | Yes (major upgrade) | SI-2 |
| PYSEC-2026-1605 — ### Impact  `Schema.load(data, many=True)` is vulnerable to denial of service attacks. A | `marshmallow` | 3.21.1 | Medium | Version bump: 3.21.1 -> 3.26.2, 4.1.2 | No | SI-2, SC-5 |
| PYSEC-2026-1845 — pytest through 9.0.2 on UNIX relies on directories with the `/tmp/pytest-of-{user}` name | `pytest` | 8.1.1 | Medium | Version bump: 8.1.1 -> 9.0.3 | Yes (major upgrade) | SI-2 |
| PYSEC-2026-2270 — python-dotenv reads key-value pairs from a .env file and can set them as environment var | `python-dotenv` | 1.0.1 | Medium | Version bump: 1.0.1 -> 1.2.2 | No | SI-2 |
| PYSEC-2026-1872 — ### Impact  Due to a URL parsing issue, Requests releases prior to 2.32.4 may leak .netr | `requests` | 2.31.0 | Medium | Version bump: 2.31.0 -> 2.32.4 | No | SI-2, SI-11 |
| PYSEC-2026-1873 — When using a `requests.Session`, if the first request to a given origin is made with `ve | `requests` | 2.31.0 | Medium | Version bump: 2.31.0 -> 2.32.0 | No | SI-2 |
| PYSEC-2026-2275 — Requests is a HTTP library. Prior to version 2.33.0, the `requests.utils.extract_zipped_ | `requests` | 2.31.0 | Medium | Version bump: 2.31.0 -> 2.33.0 | No | SI-2 |
| PYSEC-2026-2151 — Flask is a web server gateway interface (WSGI) web application framework. In versions 3. | `flask` | 3.0.2 | Low | Version bump: 3.0.2 -> 3.1.3 | No | SI-2 |

### frontend/admin-dashboard — 8 findings (8 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| CVE-2026-50170 — @angular/common: Information Leak via Default Caching of Credentialed Requests in HttpTr | `@angular/common` | 17.3.12 | High | Version bump: 17.3.12 -> 22.0.0-rc.2, 20.3.22, 19.2.23, 21.2.15 | Yes (major upgrade) | SI-2, SI-11 |
| CVE-2026-50171 — @angular/common: Angular @angular/common: Denial of Service via malformed digitsInfo par | `@angular/common` | 17.3.12 | High | Version bump: 17.3.12 -> 22.0.0-rc.2, 20.3.22, 19.2.23, 21.2.15 | Yes (major upgrade) | SI-2, SC-5 |
| CVE-2026-54266 — @angular/common: Weak 32-Bit Cache Key Hashing in `HttpTransferCache` Leading to Cross-R | `@angular/common` | 17.3.12 | High | Version bump: 17.3.12 -> 22.0.1, 21.2.17, 20.3.25 | Yes (major upgrade) | SI-2, SI-11 |
| CVE-2026-54268 — @angular/common: Angular @angular/common: Denial of Service via crafted date format stri | `@angular/common` | 17.3.12 | High | Version bump: 17.3.12 -> 22.0.1, 21.2.17, 20.3.25 | Yes (major upgrade) | SI-2, SC-5 |
| CVE-2026-68945 — @angular/common: Angular: Cross-Request Response Reuse and State Poisoning in HttpTransf | `@angular/common` | 17.3.12 | High | Version bump: 17.3.12 -> 22.0.2, 21.2.19, 20.3.27 | Yes (major upgrade) | SI-2 |
| CVE-2026-69151 — @angular/compiler: @angular/core: Angular: Cross-Site Scripting via internationalization | `@angular/compiler` | 17.3.12 | High | Version bump: 17.3.12 -> 22.0.1, 21.2.19, 20.3.27 | Yes (major upgrade) | SI-2, SI-10 |
| CVE-2026-54267 — @angular/core: Angular Client Hydration DOM Clobbering & Response-Cache Poisoning | `@angular/core` | 17.3.12 | High | Version bump: 17.3.12 -> 22.0.1, 21.2.17, 20.3.25 | Yes (major upgrade) | SI-2 |
| CVE-2026-69151 — @angular/compiler: @angular/core: Angular: Cross-Site Scripting via internationalization | `@angular/core` | 17.3.12 | High | Version bump: 17.3.12 -> 22.0.1, 21.2.19, 20.3.27 | Yes (major upgrade) | SI-2, SI-10 |

### frontend/client-app — 3 findings (3 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| CVE-2026-67213 — nanoid: nanoid: Denial of Service via infinite loop in random ID generation | `nanoid` | 3.3.16 | High | Version bump: 3.3.16 -> 3.3.17, 5.1.6 | No | SI-2, SC-5 |
| GHSA-qwww-vcr4-c8h2 — React Router: RSC Mode CSRF Bypass Allows Action Execution Before 400 Response | `react-router` | 7.18.1 | High | Version bump: 7.18.1 -> 7.18.2, 8.3.0 | No | SI-2, AC-3 |
| CVE-2026-69185 — socket.io-parser: Socket.IO: Denial of Service via memory exhaustion from crafted packet | `socket.io-parser` | 4.2.6 | High | Version bump: 4.2.6 -> 4.2.7, 3.4.5, 3.3.6 | No | SI-2, SC-5 |

### demo-platform/dashboard — 10 findings (10 high/critical)

| Finding | Package | Installed | Severity | Fix | Breaking? | Controls (NIST 800-53) |
|---|---|---|---|---|---|---|
| CVE-2026-59873 — tar: node-tar: Denial of Service via crafted gzip bomb | `tar` | 7.5.11 | Critical | Version bump: 7.5.11 -> 7.5.19 | No | SI-2, SC-5 |
| GHSA-5p4m-2wfm-xmqj — JS-YAML: Quadratic CPU consumption in !!omap resolution (3.x and 4.x) — CVE-2026-59870 f | `js-yaml` | 4.3.0 | High | Version bump: 4.3.0 -> 4.3.1, 3.15.1 | No | SI-2, SC-5 |
| CVE-2026-67213 — nanoid: nanoid: Denial of Service via infinite loop in random ID generation | `nanoid` | 3.3.16 | High | Version bump: 3.3.16 -> 3.3.17, 5.1.6 | No | SI-2, SC-5 |
| CVE-2026-64641 — next: Next.js: Denial of Service via crafted requests to App Router with Server Actions | `next` | 15.5.20 | High | Version bump: 15.5.20 -> 15.5.21, 16.2.11 | No | SI-2, SC-5 |
| CVE-2026-64645 — next: Next.js: Server-Side Request Forgery vulnerability | `next` | 15.5.20 | High | Version bump: 15.5.20 -> 15.5.21, 16.2.11 | No | SI-2, AC-3 |
| CVE-2026-64649 — next: Next.js: Server-Side Request Forgery via malicious host redirection in Server Acti | `next` | 15.5.20 | High | Version bump: 15.5.20 -> 15.5.21, 16.2.11 | No | SI-2, AC-3 |
| CVE-2026-45623 — postcss: PostCSS: Information disclosure and denial of service via crafted CSS input | `postcss` | 8.4.31 | High | Version bump: 8.4.31 -> 8.5.12 | No | SI-2, SC-5 |
| GHSA-r28c-9q8g-f849 — PostCSS: Path Traversal in Previous Source Map Auto-Loading (sourceMappingURL) leads to  | `postcss` | 8.4.31 | High | Version bump: 8.4.31 -> 8.5.18 | No | SI-2, SI-10 |
| GHSA-f88m-g3jw-g9cj — sharp inherited vulnerabilities in libvips: CVE-2026-33327, CVE-2026-33328, CVE-2026-355 | `sharp` | 0.34.5 | High | Version bump: 0.34.5 -> 0.35.0 | No | SI-2 |
| CVE-2026-59874 — tar: Node-tar: Denial of Service via malformed tar archive header | `tar` | 7.5.11 | High | Version bump: 7.5.11 -> 7.5.18 | No | SI-2, SC-5 |

## Summary

| Service | Ecosystem | Findings | High/Critical |
|---|---|---|---|
| services/admin-service | Ruby (Bundler) | 36 | 9 |
| services/api-gateway | Go (modules) | 6 | 6 |
| services/collab-service | Node.js (npm) | 20 | 13 |
| services/document-service | Python (Poetry) | 2 | 2 |
| services/file-service | Rust (Cargo) | 1 | 1 |
| services/legacy-portal | Java (Maven) | 47 | 47 |
| services/report-service | Java (Maven) | 58 | 58 |
| services/search-service | Python (pip) | 10 | 0 |
| frontend/admin-dashboard | Node.js (npm/Angular) | 8 | 8 |
| frontend/client-app | Node.js (npm) | 3 | 3 |
| demo-platform/dashboard | Node.js (npm) | 10 | 10 |
| **Total** | | **201** | **157** |

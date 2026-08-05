# OtterWorks Application-Layer Modernization Inventory

Scope: `services/report-service` (legacy Spring Boot 2.5 / Java 8),
`services/search-service` (the repo's only Flask service), and the CI/security
guardrails that currently exempt the legacy service. Every claim below was
verified against the source at the cited file and line numbers.

---

## 1. services/report-service — Upgrade Axes

Source of truth: `services/report-service/pom.xml` and
`services/report-service/UPGRADE_GUIDE.md`. All 11 axes claimed in the guide
were verified against the source tree; findings match the guide with the
evidence below.

### Axis 1 — Java 8 → 17

- Current: `<java.version>1.8</java.version>` — `services/report-service/pom.xml:24-26`; compiler plugin also pins 1.8 at `pom.xml:193-194`.
- Target: Java 17 (prerequisite for Spring Boot 3 and Mockito 5).
- Files: `pom.xml` only.
- Depends on: nothing — this is the root prerequisite.
- Verify: `cd services/report-service && mvn compile && java -version` (reports 17+).

### Axis 2 — Spring Boot 2.5.14 → 3.2+

- Current: parent `spring-boot-starter-parent:2.5.14` — `pom.xml:7-13`.
- Files: `pom.xml`; `src/main/java/com/otterworks/report/config/SecurityConfig.java`
  extends the removed `WebSecurityConfigurerAdapter` (`SecurityConfig.java:22`), uses
  `authorizeRequests()` (`:33`) and `antMatchers(...)` (`:34-36`) — all removed/renamed
  in Spring Security 6 (`SecurityFilterChain` bean, `authorizeHttpRequests()`,
  `requestMatchers()`).
- Depends on: Axis 1 (Java 17) and Axis 3 (jakarta namespace) — Spring Boot 3 requires both.
- Verify: `mvn spring-boot:run` starts; `mvn test`; `curl http://localhost:8080/health`.

### Axis 3 — javax → jakarta

- Current imports (verified):
  - `model/Report.java:6-17` — `javax.persistence.{Column,Entity,EnumType,Enumerated,GeneratedValue,GenerationType,Id,Lob,Table,Temporal,TemporalType}` and `javax.validation.constraints.NotNull`.
  - `model/ReportRequest.java:6-7` — `javax.validation.constraints.{NotBlank,NotNull}`.
  - `controller/ReportController.java:31` — `javax.validation.Valid`.
  - `service/ReportService.java:18` — `javax.transaction.Transactional`.
  - `pom.xml:67-72` — explicit `javax.servlet:javax.servlet-api:4.0.1` dependency (remove; Boot 3 brings jakarta.servlet).
- Depends on: done together with (and required by) Axis 2.
- Verify: `grep -rn "import javax\." services/report-service/src/main/java/ | grep -v javax.sql` returns nothing; `mvn compile && mvn test`.

### Axis 4 — JUnit 4 → JUnit 5

- Current: `junit:junit:4.13.2` — `pom.xml:150-155`; JUnit 5 explicitly excluded from
  `spring-boot-starter-test` — `pom.xml:161-166`; surefire pinned at 2.22.2 — `pom.xml:200`.
- Test files (all 5, verified): `ReportServiceTest.java:7-8,14,44` and
  `controller/ReportControllerIntegrationTest.java:7-8,14,43` use
  `@RunWith(SpringRunner.class)`; `service/{Pdf,Excel,Csv}ReportGeneratorTest.java`
  use `org.junit.{After,Before,Test}` (lines 7-9 / 11-13 / 7-9).
- Depends on: none technically; naturally paired with Axis 2 and Axis 11.
- Verify: `mvn test` — Jupiter engine runs all tests, no vintage-engine warnings.

### Axis 5 — SpringFox 3.0.0 → springdoc-openapi 2.x

- Current: `springfox-boot-starter:3.0.0` — `pom.xml:30,82-86`.
- Files: `config/SwaggerConfig.java` (Docket bean, delete);
  `controller/ReportController.java:8-12` (`io.swagger.annotations.{Api,ApiOperation,ApiParam,ApiResponse,ApiResponses}`);
  `model/Report.java:3-4`, `model/ReportRequest.java:3-4`, `model/ReportResponse.java:3-4`
  (`ApiModel`/`ApiModelProperty` → `@Schema`).
- Depends on: Axis 2 (SpringFox is dead and incompatible with Boot 3; also removes the
  `spring.mvc.pathmatch.matching-strategy=ant-path-matcher` workaround).
- Verify: `mvn spring-boot:run && curl http://localhost:8080/v3/api-docs` returns OpenAPI 3 JSON.

### Axis 6 — iText 5.5.13.3 → OpenPDF (or iText 7)

- Current: `com.itextpdf:itextpdf:5.5.13.3` (AGPL) — `pom.xml:33-34,101-105`.
- Files: `service/PdfReportGenerator.java:3-15` — 13 `com.itextpdf.text.*` imports
  (→ `com.lowagie.text.*` under OpenPDF; near-drop-in).
- Depends on: nothing (independent package rename).
- Verify: `mvn test` — `PdfReportGeneratorTest` passes; generated PDF opens.

### Axis 7 — Commons Lang 2.6 → commons-lang3 3.14+

- Current: `commons-lang:commons-lang:2.6` — `pom.xml:36,108-112`.
- Files (verified imports): `util/ReportDateUtils.java:3-5`
  (`StringUtils`, `time.DateFormatUtils`, `time.DateUtils`),
  `service/PdfReportGenerator.java:18`, `service/ExcelReportGenerator.java:5`,
  `service/ReportDataFetcher.java:8` (all `org.apache.commons.lang.StringUtils`).
- Depends on: nothing (package rename, API-compatible).
- Verify: `grep -rn "org.apache.commons.lang\." services/report-service/src/main/java/ | grep -v lang3` returns nothing; `mvn test`.

### Axis 8 — Commons IO 2.6 → 2.15+

- Current: `<commons-io.version>2.6</commons-io.version>` — `pom.xml:38,115-119`.
- Files: `pom.xml` version bump only; consumer is
  `controller/ReportController.java:13` (`org.apache.commons.io.FileUtils`) — no import change.
- Depends on: nothing.
- Verify: `mvn dependency:tree | grep commons-io` shows 2.15.x; `mvn test`.

### Axis 9 — Guava 28.0-jre → 33+

- Current: `<guava.version>28.0-jre</guava.version>` — `pom.xml:40,122-126`.
- Files: `pom.xml` version bump; consumer is `service/ReportDataFetcher.java:3-5`
  (`CacheBuilder`, `CacheLoader`, `LoadingCache` — stable in 33; optional Caffeine migration).
- Depends on: nothing.
- Verify: `mvn dependency:tree | grep guava` shows 33.x; `mvn test`.

### Axis 10 — Apache POI 4.1.2 → 5.2+

- Current: `<poi.version>4.1.2</poi.version>` — `pom.xml:32,89-98` (`poi` + `poi-ooxml`).
- Files: `pom.xml` version bump; consumer `service/ExcelReportGenerator.java` (XSSF APIs stable).
- Depends on: nothing.
- Verify: `mvn dependency:tree | grep poi` shows 5.2.x; `mvn test`.

### Axis 11 — Mockito 3.12.4 → 5.x

- Current: `mockito-core:3.12.4` — `pom.xml:173-178`.
- Files: `pom.xml` (bump + add `mockito-junit-jupiter`); test files switch
  `@RunWith(MockitoJUnitRunner.class)` → `@ExtendWith(MockitoExtension.class)` where used.
- Depends on: Axis 1 (Mockito 5 requires Java 11+) and pairs with Axis 4.
- Verify: `mvn test`.

---

## 2. services/search-service — Flask → FastAPI Mapping

The only Flask service in the repo (`services/search-service/requirements.txt:1-3`:
`flask==3.0.2`, `flask-restful==0.3.10`, `flask-cors==4.0.2`). In-repo FastAPI
reference: `services/document-service` (`app/main.py`, `app/api/*.py`).

### Endpoints

| # | Method | Path | Handler (file:line) | Flask constructs used | FastAPI equivalent (document-service reference) |
|---|--------|------|---------------------|-----------------------|--------------------------------------------------|
| 1 | GET | `/health` | `app/api/health.py:39-45` `health()` | `health_bp` Blueprint (`health.py:15`), `jsonify` | `APIRouter()` + `@router.get("/health")` returning a dict (`document-service/app/api/health.py:12-16`) |
| 2 | GET | `/health/ready` | `app/api/health.py:48-59` `readiness()` | `current_app.config.get("SEARCH_SERVICE")` (`health.py:51`), `jsonify`, tuple status `503` | dependency/`app.state` access + `JSONResponse(status_code=503)` or `HTTPException` |
| 3 | GET | `/metrics` | `app/api/health.py:62-69` `metrics()` | raw tuple `(body, 200, headers)` | `Response(generate_latest(), media_type="text/plain")` |
| 4 | GET | `/api/v1/search/` | `app/api/search.py:44-80` `search_documents()` | `search_bp` Blueprint (`search.py:16`), `request.args.get`, `request.headers.get("X-User-ID")`, `jsonify` + status tuples, `current_app.config["SEARCH_SERVICE"]` (`search.py:41`) | `APIRouter` with prefix set in `include_router` (`document-service/app/main.py:68`); `Query(...)` params; `Request`-based `X-User-ID` extraction (`document-service/app/api/documents.py:65-90`); return Pydantic model/dict |
| 5 | GET | `/api/v1/search/suggest` | `app/api/search.py:83-116` `suggest()` | `request.args.get`, `jsonify`; contains chaos-flag branch (`search.py:98-108`) — preserve as-is | `Query(min_length=2)` validation; keep chaos branch (planted-bug lab feature) |
| 6 | POST | `/api/v1/search/advanced` | `app/api/search.py:119-157` `advanced_search()` | `request.get_json()`, `request.headers.get("X-User-ID")`, `jsonify` | Pydantic request-body schema (cf. `document-service/app/schemas/document.py`) + `Request` header extraction |
| 7 | GET | `/api/v1/search/analytics` | `app/api/search.py:160-168` `search_analytics()` | `jsonify` | plain `@router.get` returning dict |
| 8 | POST | `/api/v1/search/index/document` | `app/api/index.py:25-42` `index_document()` | `index_bp` Blueprint (`index.py:14`), `request.get_json()`, `jsonify`, `current_app.config["SEARCH_SERVICE"]` (`index.py:21`) | Pydantic body model; `status_code=201` via `@router.post(..., status_code=status.HTTP_201_CREATED)` (cf. `document-service/app/api/templates.py`) |
| 9 | POST | `/api/v1/search/index/file` | `app/api/index.py:45-62` `index_file()` | same as above | same as above |
| 10 | DELETE | `/api/v1/search/index/<doc_type>/<doc_id>` | `app/api/index.py:65-80` `remove_from_index()` | Flask path converters `<doc_type>/<doc_id>`, `jsonify`, 404 tuple | path params `{doc_type}/{doc_id}` typed in signature; `HTTPException(404)` |
| 11 | POST | `/api/v1/search/reindex` | `app/api/index.py:83-93` `reindex()` | `jsonify` | plain `@router.post` |

### App-level constructs

| Flask construct | Location | FastAPI equivalent |
|-----------------|----------|--------------------|
| `Flask(__name__)` app factory `create_app()` | `app/main.py:51-122` | module-level `FastAPI(...)` with `lifespan` context manager (`document-service/app/main.py:30-57`) |
| `flask_cors.CORS(app, origins=[...])` | `app/main.py:59` | `app.add_middleware(CORSMiddleware, allow_origins=...)` (`document-service/app/main.py:59-65`) |
| `app.config["APP_CONFIG"]` / `app.config["SEARCH_SERVICE"]` | `app/main.py:62,66` | `app.state` attributes or module-level pydantic-settings singleton (`document-service/app/config.py:3-30`) plus `Depends` providers |
| `app.register_blueprint(bp, url_prefix=...)` | `app/main.py:76-78` | `app.include_router(router, prefix=..., tags=...)` (`document-service/app/main.py:67-70`) |
| `@app.before_request` auth hook (`require_auth`) | `app/middleware/auth.py:29-61`, wired at `main.py:81` | HTTP middleware (`@app.middleware("http")`) or a shared `Depends` on routers; public-prefix exemption logic ports directly |
| `@app.before_request` / `@app.after_request` Prometheus timing | `app/main.py:84-97` | single `@app.middleware("http")` wrapping `call_next`, using `request.scope["route"].path` for the endpoint label |
| SQS consumer started in factory | `app/main.py:100-114` | start/stop in the `lifespan` context manager (startup before `yield`, shutdown after) |
| `app.run(host, port, debug)` | `app/main.py:125-128` | `uvicorn.run("app.main:app", host=..., port=...)` |
| Chaos flag reads via Redis (`_chaos_active`) | `app/api/search.py:31-36,98` | framework-agnostic; keep unchanged (lab feature — do not "fix") |

- Verify (behavior parity): `cd services/search-service && .venv/bin/pytest` (test
  suite in `services/search-service/tests/`); runtime:
  `curl localhost:8087/health` and `curl -H "X-User-ID: u1" "localhost:8087/api/v1/search/?q=test"`
  (default port 8087 per `app/config.py:54`).
- Verify (no Flask remains): `grep -rn "from flask\|import flask" services/search-service/app/` returns nothing.

Note: `flask-restful` is declared (`requirements.txt:2`) but no `flask_restful`
import exists in `app/` — it can simply be dropped.

---

## 3. Guardrails Exempting the Legacy Service

These must be removed **after** the report-service upgrade lands, or the upgrade
is invisible to CI/security scanning.

| Guardrail | Evidence | What to change | Verification command |
|-----------|----------|----------------|----------------------|
| Trivy `--skip-dirs services/report-service` (3 occurrences: PR-head scan, merge-base scan, full-baseline scan) | `.github/workflows/security-scan.yml:37,52,188` | Delete all three `--skip-dirs services/report-service` lines | `grep -n "skip-dirs services/report-service" .github/workflows/security-scan.yml` returns nothing |
| `.trivyignore` note documenting the exclusion | `.trivyignore:3` ("report-service is excluded via skip-dirs (intentional legacy Java 8 service for upgrade exercise)") | Remove the stale comment (and re-scan for any report-service CVEs that then surface) | `grep -n "report-service" .trivyignore` returns nothing |
| Makefile `security-scan` target skips report-service | `Makefile:227` (`@echo "=== Report Service (skipped - legacy) ==="`; target begins `Makefile:214`) | Replace the echo with a real scan step (e.g. `cd services/report-service && trivy fs .` or `mvn org.owasp:dependency-check-maven:check`) | `make security-scan` output no longer contains "skipped - legacy" |
| CI pins Java 8 for the report-service job | `.github/workflows/ci.yml:332` (`java-version: '8'` in the `report-service` job, `ci.yml:320-335`) | Change to `java-version: '17'` | `grep -n "java-version" .github/workflows/ci.yml` shows `'17'` for the report-service job; CI job green |

---

## 4. Execution Sequence (ordered), with Risk

| Seq | Work item | Depends on | Risk | Why |
|-----|-----------|------------|------|-----|
| 1 | report-service: Java 8 → 17 (Axis 1) | — | Medium | Root prerequisite; Java 17 module system can surface reflection issues in old deps |
| 2 | report-service: javax → jakarta (Axis 3) | 1 | Medium | Mechanical rename across 4 source files + pom; must land atomically with #3 |
| 3 | report-service: Spring Boot 2.5 → 3.2 (Axis 2) | 1, 2 | **High** | Spring Security 6 rewrite of `SecurityConfig.java`; largest behavioral surface |
| 4 | report-service: SpringFox → springdoc (Axis 5) | 3 | Medium | SpringFox is incompatible with Boot 3 — blocking, must follow immediately |
| 5 | report-service: JUnit 4 → 5 (Axis 4) | — (pairs with 3) | Low | Mechanical annotation/import swap in 5 test files + surefire 3.x |
| 6 | report-service: Mockito 3 → 5 (Axis 11) | 1, 5 | Low | Version bump; stricter stubbing may flag unused stubs |
| 7 | report-service: commons-lang 2 → 3 (Axis 7) | — | Low | Package rename, API-compatible, 4 files |
| 8 | report-service: iText 5 → OpenPDF (Axis 6) | — | Medium | 13-import rename; PDF output should be diffed visually |
| 9 | report-service: commons-io 2.6 → 2.15 (Axis 8) | — | Low | Version bump only |
| 10 | report-service: Guava 28 → 33 (Axis 9) | — | Low | Version bump only (cache APIs stable) |
| 11 | report-service: POI 4.1.2 → 5.2 (Axis 10) | — | Low | Version bump; watch deprecation warnings in ExcelReportGenerator |
| 12 | Guardrails: unpin Java in ci.yml report-service job | 1-3 | Low | One-line change; proves upgrade in CI |
| 13 | Guardrails: remove Trivy skip-dirs (×3) + .trivyignore note + Makefile skip | 1-11 | Medium | Newly-scanned legacy deps may surface CRITICAL/HIGH CVEs that gate PRs — do last, triage findings |
| 14 | search-service: Flask → FastAPI rewrite (11 endpoints + middleware + SQS lifespan) | — (independent track) | **High** | Auth before_request semantics, Prometheus labels, chaos-flag branch, and gateway routing must all be preserved; validate with existing pytest suite |

After each report-service step: `cd services/report-service && mvn clean test`.
Full-repo gates: `make test`, `make lint`, `make security-scan`.

**Caution (golden-app policy, AGENTS.md):** the chaos branch in
`app/api/search.py:98-108` is a planted lab feature — any FastAPI port must carry
it over unchanged, not remove it.

# Migration Notes — Java 8 / Spring Boot 2.5.14 → Java 17 / Spring Boot 3.2.5

Each breaking change hit during the upgrade, the file it was in, and why the fix
is behavior-preserving. Scope was deliberately minimal: only dependencies that are
actually **incompatible** with Spring Boot 3 / Java 17 were changed. Compatible
legacy libraries (POI 4.1.2, iText 5, Guava 28, Commons Lang 2, Commons IO 2.6,
OpenCSV 4.6) were left at their current versions to avoid any behavior change.

## 1. Java version and Spring Boot parent — `pom.xml`

- `spring-boot-starter-parent` 2.5.14 → **3.2.5**; `java.version` /
  `maven.compiler.source|target` 1.8 → **17**.
- Removed pinned `maven-compiler-plugin` 3.8.1 and `maven-surefire-plugin` 2.22.2
  versions so the Boot 3.2 parent's managed versions apply (compiler 3.11,
  surefire 3.1). Surefire 2.x would not discover tests through the JUnit
  Platform (see §6).
- Behavior-preserving: build tooling only; no source semantics change. All 44
  tests pass unchanged.

## 2. `javax.*` → `jakarta.*` (Jakarta EE 9 namespace)

Spring Boot 3 ships Jakarta EE 10; the `javax.*` classes no longer exist on the
classpath. Import-only renames (identical annotation semantics):

| File | Change |
|---|---|
| `model/Report.java` | `javax.persistence.*` → `jakarta.persistence.*`, `javax.validation.constraints.NotNull` → `jakarta.validation.constraints.NotNull` |
| `model/ReportRequest.java` | `javax.validation.constraints.{NotBlank,NotNull}` → `jakarta.validation.constraints.*` |
| `controller/ReportController.java` | `javax.validation.Valid` → `jakarta.validation.Valid` |
| `service/ReportService.java` | `javax.transaction.Transactional` → `jakarta.transaction.Transactional` (same JTA annotation, relocated; **not** switched to Spring's `@Transactional` to keep identical propagation semantics — REQUIRED — and rollback rules) |
| `pom.xml` | Removed `javax.servlet:javax.servlet-api` (provided-scope). Boot 3's starter-web supplies `jakarta.servlet-api`; the project has no direct servlet-API imports, so nothing else changed. |

## 3. `WebSecurityConfigurerAdapter` removed — `config/SecurityConfig.java`

Spring Security 6 removed `WebSecurityConfigurerAdapter` and the
`authorizeRequests()`/`antMatchers()` chain API. Rewritten as a
`@Bean SecurityFilterChain` with the lambda DSL:

- Same rules, one-for-one: CSRF disabled, stateless sessions, identical
  permit-all matchers, frame-options DENY, content-type options,
  XSS protection in block mode (`ENABLED_MODE_BLOCK` = the old `.block(true)`).
- `antMatchers(...)` → `requestMatchers(...)` (equivalent path matching for
  these literal/`/**` patterns).
- Added explicit `.anyRequest().permitAll()`: in Security 5, requests matching
  no rule were not restricted; Security 6's `authorizeHttpRequests` denies
  unmatched requests by default, so the explicit rule preserves the old
  behavior for endpoints like `/v3/api-docs`.
- Swagger matchers updated from SpringFox paths (`/swagger-resources/**`,
  `/v2/api-docs/**`) to springdoc paths (`/swagger-ui.html`, `/v3/api-docs/**`)
  — see §4. Old paths no longer exist so no access change for real endpoints.

## 4. SpringFox → springdoc-openapi — `pom.xml`, `config/SwaggerConfig.java`, controller + models

SpringFox 3.0.0 is dead and hard-fails on Spring Boot 3 (relies on removed
Spring MVC internals). Replaced with
`org.springdoc:springdoc-openapi-starter-webmvc-ui:2.3.0`.

- `SwaggerConfig.java`: `Docket`/`ApiInfo` bean → `OpenAPI`/`Info` bean with the
  same title, description, version, and contact.
- `controller/ReportController.java`: `@Api`→`@Tag`, `@ApiOperation`→`@Operation`,
  `@ApiResponse(code=..)`→`@ApiResponse(responseCode="..")`, `@ApiParam`→`@Parameter`.
- `model/{Report,ReportRequest,ReportResponse}.java`:
  `@ApiModel`/`@ApiModelProperty` → `@Schema` (value→description,
  required→`RequiredMode.REQUIRED`, readOnly→`AccessMode.READ_ONLY`).
- Behavior-preserving for the API itself: these are documentation-only
  annotations; request/response handling, validation, and JSON serialization are
  untouched. The docs endpoint moves from `/v2/api-docs` (Swagger 2 JSON) to
  `/v3/api-docs` (OpenAPI 3 JSON) — an unavoidable, documented difference of
  retiring SpringFox.
- `application.properties` / `application-test.properties`: removed
  `spring.mvc.pathmatch.matching-strategy=ant-path-matcher`, which existed only
  as a SpringFox workaround (and the property's ant-path option is gone in
  Boot 3).

## 5. Apache HttpClient 4 → 5 — `pom.xml`, `config/AppConfig.java`

Spring Framework 6 removed HttpClient 4.x support from
`HttpComponentsClientHttpRequestFactory`; it now requires HttpClient 5.

- Dependency: `org.apache.httpcomponents:httpclient:4.5.13` →
  `org.apache.httpcomponents.client5:httpclient5` (version managed by the Boot
  parent).
- `AppConfig.restTemplate()`: same pool sizing (`maxTotal=50`,
  `defaultMaxPerRoute=20`). The factory's removed `setConnectTimeout(int)` /
  `setReadTimeout(int)` are expressed at the connection-manager level instead:
  `ConnectionConfig.setConnectTimeout` (same 5 s default) and
  `SocketConfig.setSoTimeout` (same 30 s default, the HttpClient-5 equivalent of
  the read timeout). Same timeouts, same pooling — the `RestTemplate` consumer
  code (`ReportDataFetcher`) is unchanged.

## 6. JUnit 4 tests under Boot 3 — `pom.xml` only (test sources unmodified)

`spring-boot-starter-test` 3.x no longer ships a JUnit 4 runtime, and the old
pom explicitly excluded JUnit Jupiter. Rather than rewriting the five test
classes (which would risk changing what is asserted), the tests were kept
byte-for-byte identical and run through the **JUnit Vintage engine**:

- Removed the `junit-jupiter` exclusion from `spring-boot-starter-test`.
- Added `org.junit.vintage:junit-vintage-engine` (test scope, managed version),
  which executes JUnit 4 `@RunWith(SpringRunner.class)` / `@Before` / `@After`
  tests unchanged on the JUnit 5 platform.
- Dropped the pinned `mockito-core:3.12.4` version (Mockito 3 does not support
  Java 17 class files); the Boot-managed Mockito 5 is used. No test in this
  module imports Mockito, so this is a classpath-only change.

## 7. Hibernate 5.4 → 6.4 (transitive via starter-data-jpa)

One mapping change was required: Hibernate 6 binds `@Lob String` on PostgreSQL
through the large-object (`oid`) JDBC API instead of Hibernate 5's `text`
column mapping. `Report.errorMessage` (`model/Report.java`) therefore replaces
`@Lob` with `@JdbcTypeCode(SqlTypes.LONG32VARCHAR)`, which maps to an unbounded
`text` column on PostgreSQL (plain `LONGVARCHAR` would produce a length-limited
`varchar(32600)` under Hibernate 6's DDL type registry) — matching the column
the pre-upgrade version created and avoiding both runtime read/write failures
on existing databases and leaked server-side large objects on fresh ones.

Everything else needed no change: the entity's other mappings
(`@Enumerated(STRING)`, `@Temporal`) are portable and the repository uses
derived queries plus simple JPQL, all valid under Hibernate 6.
`PostgreSQLDialect` / `H2Dialect` names in the properties files are unchanged
and still correct in Hibernate 6. Verified by the H2-backed integration tests
(create, list, fetch, download-state and delete flows).

## 8. Dockerfile — `services/report-service/Dockerfile`

`maven:3.8.7-eclipse-temurin-8` → `maven:3.9.6-eclipse-temurin-17` (builder) and
`eclipse-temurin:8-jre` → `eclipse-temurin:17-jre` (runtime). Same distro
family, same non-root user, port, healthcheck, and entrypoint.

## 9. CI — `.github/workflows/ci.yml` (report-service job)

`setup-java` `java-version: '8'` → `'17'`. Build/test/package steps unchanged.

## Flagged: things NOT changed (would alter behavior or exceed scope)

- **iText 5.5.13.3 (AGPL)**: still works on Java 17/Boot 3, so it was kept to
  preserve identical PDF output. The AGPL licensing concern remains; migrating
  to OpenPDF would change the PDF producer metadata and is left as follow-up.
- **Guava 28, Commons Lang 2, Commons IO 2.6, OpenCSV 4.6, POI 4.1.2**: all
  compile and run correctly on the new stack; upgrading them is a CVE-hygiene
  task, not a Boot-3 requirement, and was deliberately skipped to keep report
  output byte-comparable.
- **JUnit 4 test style**: intentionally kept (via Vintage engine) instead of a
  Jupiter rewrite, so the test suite itself is provably identical pre/post
  upgrade.
- **`/v2/api-docs` → `/v3/api-docs`**: unavoidable consequence of SpringFox's
  incompatibility; no application client consumes the docs endpoint.

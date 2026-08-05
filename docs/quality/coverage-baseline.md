# Test Coverage Baseline

Baseline measured on `main` via `make test-coverage` (plus per-service native test
runs for services the Makefile target does not cover). Date: 2026-08-05.

## How this was measured

`make test-coverage` only exercises 7 of the 12 backend services under
`services/` (document, search, collab, api-gateway, admin, auth, file). The
remaining 5 (analytics, notification, audit, report, legacy-portal) were run
with their native test commands to complete the baseline; where noted, their
build files have no coverage plugin configured, so tests run but no line
coverage is reported.

## 1. Per-service baseline

| Service | Language | Test framework / coverage tool | Test command (from repo root) | Line coverage | Test files | Run succeeded | After |
|---|---|---|---|---|---|---|---|
| admin-service | Ruby (Rails API) | RSpec + SimpleCov | `cd services/admin-service && bundle exec rspec` | 36.8% (426/1157 lines) | 16 spec files (120 examples) | Yes — 120 passed. Requires Postgres up (`make infra-up`) and `DATABASE_PASSWORD=<local dev password from docker-compose.infra.yml> RAILS_ENV=test bundle exec rake db:create db:schema:load` first | — |
| analytics-service | Scala | ScalaTest via sbt — **no scoverage plugin** | `cd services/analytics-service && sbt test` | not reported | 9 spec files (56 tests) | Yes — 56 passed; coverage not measurable (no scoverage in `build.sbt`/`project/plugins.sbt`) | — |
| api-gateway | Go | `go test -cover` | `cd services/api-gateway && go test -cover ./...` | health 100%, middleware 69.7%, proxy 56.8%, config 0%, cmd/server 0% | 6 `_test.go` files | Yes | — |
| audit-service | C# (.NET 8) | xUnit + coverlet | `cd services/audit-service/tests/AuditService.Tests && dotnet test --collect:"XPlat Code Coverage"` | 54.5% | 3 test files (24 tests) | Yes — 24 passed (needs .NET 8 runtime; not in `make test-coverage` target) | — |
| auth-service | Java (Spring Boot) | JUnit 5 + JaCoCo | `cd services/auth-service && ./gradlew test jacocoTestReport` | 73.3% (316/431 lines) | 3 test classes (29 tests) | Yes | — |
| collab-service | Node.js (TypeScript) | Jest (built-in coverage) | `cd services/collab-service && npm test -- --coverage` | 65.1% lines (64.0% stmts, 37.4% branch) | 3 test files (45 tests) | Yes | — |
| document-service | Python (FastAPI) | pytest + pytest-cov | `cd services/document-service && poetry run pytest --cov=app --cov-report=term-missing` | 78% | 6 test files (42 tests) | Partial — **9 of 42 tests fail** (auth: expected 200/204, got 401 on `tests/test_documents_api.py`). Bare `pytest` in the Makefile fails; deps live in the Poetry venv | — |
| file-service | Rust (Actix-web) | `cargo test` — **no coverage tool** (no tarpaulin/llvm-cov) | `cd services/file-service && cargo test` | not reported | 0 test files; 3 inline `#[cfg(test)]` modules (11 tests) | Yes — 11 passed; coverage not measurable. Requires Rust ≥1.85 (edition2024); stock 1.83 toolchain fails | **37 tests** (was 11) across 6 inline `#[cfg(test)]` modules in 4 files; all pass — see §4 |
| legacy-portal | Java (Spring Boot) | JUnit + Surefire — **no JaCoCo** | `cd services/legacy-portal && ./mvnw test` | not reported | 4 test classes (13 tests) | Yes — 13 passed (not in `make test-coverage` target) | — |
| notification-service | Kotlin (Ktor) | JUnit 5 via Gradle — **no JaCoCo** | `cd services/notification-service && ./gradlew test` | not reported | 3 test classes (28 tests) | Yes — 28 passed (not in `make test-coverage` target) | — |
| report-service | Java (Spring Boot, legacy) | JUnit + Surefire — **no JaCoCo** | `cd services/report-service && mvn test` | not reported | 5 test classes (44 tests) | Partial — **1 of 44 tests fails**: `ReportControllerIntegrationTest.downloadPendingReportReturns409` (expected 409, got 404) | — |
| search-service | Python (Flask) | pytest + pytest-cov | `cd services/search-service && .venv/bin/pytest --cov=app --cov-report=term-missing` | 75% | 5 test files (41 tests) | Yes — 41 passed | — |

### Where coverage could not be measured, and why

- **file-service (Rust)**: no coverage instrumentation exists in the repo
  (`cargo test` reports pass/fail only; no tarpaulin or `cargo llvm-cov`
  configuration). Also fails to build on Cargo < 1.85 because a dependency
  requires `edition2024`.
- **analytics-service (Scala)**: no scoverage/sbt coverage plugin configured.
- **notification-service (Kotlin)**: Gradle build has no JaCoCo plugin.
- **report-service / legacy-portal (Java, Maven)**: no JaCoCo plugin in either
  `pom.xml`.
- **`make test-coverage` toolchain failures on a fresh machine**: the target
  assumes `pytest`, `jest`, `go`, `bundle`, a working Gradle wrapper, and a
  recent Rust toolchain are all on `PATH`. On the unprepared snapshot every one
  of the seven services failed (missing `pydantic_settings`/`structlog`,
  `jest: not found`, `go: command not found`, `bundle: command not found`,
  missing `gradle-wrapper.jar`, Cargo too old). All numbers above were captured
  after installing the toolchains per the environment blueprint. In addition,
  the Document Service line in the Makefile invokes bare `pytest`, which cannot
  see the Poetry-managed virtualenv; it must be run via `poetry run pytest`.
- **admin-service**: `bundle exec rspec` reports 0% unless Postgres is running
  with the test DB created (`admin_service_test`; set `DATABASE_PASSWORD` to the
  local dev Postgres password from `docker-compose.infra.yml`),
  and Ruby 3.3.0 specifically cannot load the locked `connection_pool` 3.0.2
  gem (parser bug, fixed in 3.3.1+) — Ruby 3.3.6 works.

## 2. Weakest services (ranked, weakest first)

Ranked on untested critical paths (request handlers, persistence, event
publishing, error branches), not raw percentage.

1. **file-service (Rust)** — The entire HTTP surface is untested: all 24
   request handlers in `src/handlers.rs` (upload, download, share, trash,
   restore, folders) have zero tests; the only handler tests cover `health` and
   `metrics`. The whole persistence layer (`src/metadata.rs`, ~28 DynamoDB
   methods) is untested except for pure parsing helpers; the S3 layer
   (`src/storage.rs`) and all 7 SNS event publishers (`src/events.rs`) have no
   tests at all — only struct serialization is checked. Error mapping
   (`src/errors.rs::error_response`) and request-ID middleware are untested.
   This is the platform's core file-storage service, and there is also no
   coverage tooling to even track progress.
2. **audit-service (C#)** — 54.5% overall, but the coverage is inverted
   relative to risk: the whole request path is at 0% — every endpoint in
   `src/Controllers/AuditController.cs` (RecordEvent, QueryEvents, GetEvent,
   GetUserActivityReport, GetResourceHistory, GetComplianceReport,
   ExportAuditLog, ArchiveOldEvents), the SNS/SQS ingestion loop
   `src/Services/SnsConsumer.cs` (ExecuteAsync, GetOrCreateQueueUrlAsync,
   ProcessMessageAsync), and both middlewares (`ErrorHandlingMiddleware`,
   `RequestLoggingMiddleware`). For a compliance/audit service, unverified
   event ingestion and error handling are the highest-risk gaps.
3. **admin-service (Ruby)** — 36.8% line coverage with five controllers at 0%:
   `alerts_controller.rb`, `chaos_controller.rb`, `incidents_controller.rb`,
   `settings_controller.rb`, `health_controller.rb` (admin API), plus 0% on
   `app/services/chaos_probe_service.rb`, `devin_session_service.rb`,
   `health_checker.rb`, `admin_settings_service.rb`, and the `Incident` model.
   The JWT authenticator middleware (`app/middleware/jwt_authenticator.rb`) is
   only 31% covered, so most auth error branches are unverified.

Honorable mentions (not top-3 but notable gaps): collab-service
`src/services/redis-adapter.ts` 0% and `src/handlers/presence.ts` ~30%;
search-service `app/services/sqs_consumer.py` 20%; notification-service
repository, routes, WebSocket manager, and email sender have no direct tests.

## 3. Untested code paths in the weakest service (file-service)

All paths below are in `services/file-service/src/` and have no test coverage.

**HTTP request handlers (`handlers.rs`)** — none tested except `health`/`metrics`:
- `upload_file` — multipart parsing, `X-User-ID` header vs form-field owner
  resolution, `FileTooLarge` size-limit branch, S3 upload + metadata write +
  version write + `file_uploaded` event sequencing
- `get_file_metadata`, `list_files`, `list_shared_files`, `list_trashed`
- `download_file` — S3 fetch and presigned-URL paths
- `delete_file` — S3 delete + metadata delete + `file_deleted` event
- `move_file`, `rename_file` (rename validation and version bumping)
- `list_versions`
- `trash_file`, `restore_file` — trash lifecycle and `file_trashed`/
  `file_restored` events
- `share_file` — permission parsing, duplicate-share detection
  (`find_existing_share`), `file_shared` event; `remove_share`
- `create_folder`, `get_folder`, `update_folder`, `delete_folder`, `list_folders`
- `list_activity` — cross-entity aggregation, sorting, truncation

**Persistence (`metadata.rs`, `MetadataClient`)** — every DynamoDB operation:
`put_file`, `get_file`, `delete_file`, `trash_file`, `restore_file`,
`rename_file`, `move_file`, `list_trashed`, `list_files`, `put_folder`,
`get_folder`, `update_folder`, `delete_folder`, `list_folders`, `put_version`,
`list_versions`, `put_share`, `find_existing_share`, `list_shares_for_user`,
`list_shares_by_owner`, `delete_share`, `list_shares`, and the
conditional-write conflict branch `is_conditional_check_failed`. Only the pure
item-parsing helpers (`parse_file_metadata`, `parse_folder`,
`parse_file_version`, `parse_file_share`) are tested.

**Object storage (`storage.rs`, `S3Client`)** — zero tests: `upload_object`,
`download_object`, `presigned_download_url`, `delete_object`, `copy_object`.

**Event publishing (`events.rs`, `EventPublisher`)** — zero tests of publish
behavior: `publish`, `file_uploaded`, `file_deleted`, `file_shared`,
`file_trashed`, `file_restored`, `file_updated`, `file_moved` (only `FileEvent`
JSON serialization is tested, not SNS interaction or error handling).

**Error branches (`errors.rs`)** — `ServiceError::error_response` status-code
mapping (NotFound/BadRequest/FileTooLarge/Conflict/Internal…) untested.

**Middleware (`middleware.rs`)** — `RequestId` middleware (`new_transform`,
`call`) and `render_metrics` untested.

## 4. After: file-service test additions

26 tests were added to file-service (11 → 37), in the service's existing style
(inline `#[cfg(test)]` modules in `src/`). DynamoDB and SNS interactions are
exercised against the AWS SDK's `StaticReplayClient` HTTP mock (new
dev-dependencies: `aws-smithy-http-client`/`test-util`,
`aws-credential-types`/`test-util`, `http`), so the tests verify real request
shapes and response parsing without a live LocalStack.

### `make test-coverage` — file-service section, before vs. after

Before (on the baseline branch, prior to this change):

```
=== File Service ===
test handlers::tests::test_health_endpoint ... ok
test handlers::tests::test_metrics_endpoint ... ok

test result: ok. 11 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
```

After:

```
=== File Service ===
test metadata::dynamo_tests::test_trash_file_maps_conditional_failure_to_not_found ... ok
test handlers::tests::test_rename_file_rejects_blank_name ... ok

test result: ok. 37 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.03s
```

(Line coverage is still "not reported" for this service — `cargo test` has no
coverage instrumentation; adding tarpaulin/llvm-cov remains open. The other
six services in the target were unchanged by this work.)

### Critical paths now covered

- **Error mapping (`errors.rs`)** — `ServiceError::error_response` for all 12
  variants: 404 family, 400, 413 (with size details in the message), 401/403,
  and the three 500 backend error types, asserting both status code and JSON
  `error`/`message` body.
- **Persistence (`metadata.rs`)** — `get_file` success (item parsing + request
  targets the files table with the right key), `get_file` missing item →
  `FileNotFound`, `put_file` writes every attribute including optional
  `folder_id`, `list_files` applies `owner_id`/`is_trashed` filter expressions
  and parses multiple items, `find_existing_share` returns `None` on empty
  scan, and `trash_file` maps `ConditionalCheckFailedException` → `FileNotFound`
  (the `is_conditional_check_failed` branch). Plus parser error branches:
  invalid permission, invalid UUID, negative number, invalid datetime.
- **Event publishing (`events.rs`)** — `publish` is skipped (no HTTP call) when
  no topic is configured; `file_shared` publishes the expected message shape
  (event type, file id, `sharedWithUserId`) with no FIFO params on a standard
  topic; `.fifo` topics set `MessageGroupId`/`MessageDeduplicationId`; SNS
  failure surfaces as `ServiceError::SnsError`; serialization omits
  `name`/`mimeType`/`sizeBytes` when absent while keeping
  `folderId`/`sharedWithUserId` as explicit nulls.
- **Request handlers (`handlers.rs`)** — `resolve_owner_id` header-over-query
  precedence (the anti-spoofing rule) incl. invalid-header fallback;
  `get_file_metadata` invalid-id 400 and success path returning the file with
  its shares; `list_files` pagination (page/page_size windowing and `total`);
  `list_shared_files` and `list_activity` missing `X-User-ID` → 400;
  `rename_file` blank-name → 400.

### Still open

- `upload_file` multipart parsing, the `FileTooLarge` limit branch, and the
  S3 upload → metadata → version → event sequencing (needs S3 mock + multipart
  fixtures); `download_file`/`delete_file` S3 paths and `storage.rs`
  (`S3Client`) generally.
- Trash/restore/move/share handler success paths and the duplicate-share
  update branch in `share_file`; folder CRUD handlers; `list_activity`
  aggregation/sort/truncate on real data.
- Remaining `metadata.rs` operations (folders, versions, share list/delete
  variants) beyond the representative ones covered above.
- `middleware.rs` (`RequestId`, `render_metrics`) still has no direct tests.
- No line-coverage tooling for Rust yet (tarpaulin or `cargo llvm-cov`).

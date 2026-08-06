# Decision-Table Testing Standard

**Status:** normative for OtterWorks test work. **Owner:** WP-13 (test-coverage expansion program).
**Applies to:** every test that exercises a *decision* — a rule whose outcome flips on a numeric
threshold, an enumerated dimension, or a scope predicate.

This document is self-contained. You do not need to read the source BRD, the SOW, or
`brd-credit-decline-matrix.md` to apply it. The companion document
[`brd-credit-decline-matrix.md`](./brd-credit-decline-matrix.md) is a fully worked external
example of the same format.

---

## 1. Why this exists

OtterWorks' test suites are positive-path biased. The dominant defect shape in a rules-driven
system is not "the happy path broke" — it is:

- **off-by-one at the boundary** (`<` written where `<=` was meant),
- **scope leakage** (the rule fires for inputs it was never meant to govern),
- **partial fan-out** (the decision is recorded in service A but the downstream side effect in
  service B never happens, or happens twice).

None of the three is caught by a test that asserts "a 5 MB upload succeeds". All three are caught
by a decision table that is filled in completely.

A decision table forces you to write down, before any code: what the rule keys on, where its edges
are, what *every* downstream system is supposed to observe, and where the rule must stay silent.

---

## 2. The rule record

Every decision under test gets one rule record. Put it in the test file's module docstring / header
comment, or in a `docs/bdd/*.md` feature document when the rule spans services. Copy this template
verbatim.

```
### <RULE-ID>: <one-line statement of the rule>

**Source:** <file:line of the production code that implements the decision>
**Owning work package:** <WP-NN>
**Status:** implemented | not-implemented | planted-bug (see AGENTS.md)

**Input dimensions**

| Dimension | Type | In-scope values | Out-of-scope values |
|---|---|---|---|
| <name>    | numeric / enum / boolean | <values the rule governs> | <values it must ignore> |

**Condition**

    <the exact predicate, copied from the source, including the comparison operator>

**Expected outcome — per downstream system**

| System | Outcome when condition true | Outcome when condition false |
|---|---|---|
| <the service that owns the decision> | | |
| <every other system that should observe a side effect> | | |
| <every system that must NOT observe anything> | (must be: nothing) | (must be: nothing) |

**Boundary trio** — mandatory, see §3
**Scope negatives** — mandatory, see §4
**Open questions** — anything the spec does not answer, see §6
```

### 2.1 Rule ids

`<AREA>-<RULE>-<NNN>`, uppercase, stable forever. `AREA` is the build unit
(`FS` file-service, `GW` api-gateway, `AUTH`, `DOC`, `SRCH`, `NOTIF`, `AUDIT`, `ADMIN`, `COLLAB`,
`ANLY`, `RPT`, `PORTAL`, `ETL`, `PLAT` demo-platform, `WEB` client-app, `IAC`). Examples:
`FS-UPLOAD-001`, `ADMIN-QUOTA-001`, `GW-RATELIMIT-001`.

The rule id goes in the test name so a failure names the rule, not just the assertion:
`fs_upload_001_at_limit_is_accepted`.

### 2.2 "Per downstream system" is not optional

The most expensive rule bugs in a microservice mesh are asymmetric: the decision is right in the
service that made it and wrong (or missing, or duplicated) everywhere else. The outcome table must
list every system that is supposed to change *and* at least one that is supposed to stay untouched.
If a rule genuinely has exactly one observer, write "none" in a second row explicitly — the reader
must be able to tell "single-system rule" from "author forgot".

---

## 3. The mandatory boundary trio

For every numeric threshold `L`, three cases are required. Not two. Not "a big value and a small
value".

| Case | Input | Asserts |
|---|---|---|
| `limit-1` | `L - 1` (one unit below, in the smallest unit the input can take) | the rule does **not** fire below the edge |
| `limit`   | exactly `L` | **which side of the edge the limit itself falls on** |
| `limit+1` | `L + 1` | the rule fires above the edge |

`limit` is the case that exists solely to pin `<` vs `<=`. It is the one that catches the
off-by-one, and it is the one that gets skipped when people are in a hurry. It is mandatory.

**"One unit" means the smallest representable step of that input**: 1 byte for a byte count,
1 second for a TTL in seconds, 1 for an integer page size, 1 item for a collection cap. For a
float threshold (e.g. the gateway's `CBFailureRatio = 0.6`), state the step you chose and why —
a ratio built from counts has a *discrete* step (4/6 vs 5/6), so express the trio in the counts
that produce the ratio, not in floating-point epsilons.

Additional mandatory cases when the input is a size or count:

- **zero** (`0` bytes, empty page, empty batch) — almost always a distinct code path,
- **absent** (the parameter not supplied at all → the default applies; assert the default value,
  not merely "no error"),
- **invalid** (negative, non-numeric, overflowing the type) — assert the rejection is a 4xx with a
  useful message, not a 500 and not a silent clamp, unless a silent clamp is the documented
  behavior, in which case assert the clamp.

If the production code *clamps* rather than rejects (several OtterWorks endpoints do — see §7,
`page_size.unwrap_or(50).min(100)`), the trio applies to the clamp point: `99 → 99`, `100 → 100`,
`101 → 100`. A clamp with no test at `L+1` cannot be distinguished from a rejection.

---

## 4. The mandatory scope negatives

A rule has a stated scope: it governs some inputs and must be inert for the rest. For every
dimension named in the rule record's "out-of-scope values" column, add one case that varies **that
dimension alone**, holds every other dimension at a value that *would* fire the rule, and asserts:

1. the rule did not fire, **and**
2. no downstream side effect was produced anywhere.

Varying one dimension at a time is the point. A negative case that changes two things proves
nothing about either.

Add one **combination negative**: every in-scope dimension set to a firing value except one
out-of-scope value buried in the middle. This is the case that catches a predicate that was
refactored into an `||` where an `&&` was meant.

Assertion (2) is where scope negatives earn their keep. "Returned 200" does not prove the rule was
inert if the service still emitted an SNS event, wrote an audit row, or incremented a counter.
Assert on the observable side-effect surface, not only on the response.

---

## 5. Worked OtterWorks example

### FS-UPLOAD-001: an upload larger than `MAX_UPLOAD_BYTES` is rejected

**Source:** `services/file-service/src/handlers.rs:87`, limit from
`services/file-service/src/config.rs:49-52` (`MAX_UPLOAD_BYTES`, default `104_857_600` = 100 MB)
**Owning work package:** WP-01
**Status:** implemented, untested

**Input dimensions**

| Dimension | Type | In-scope values | Out-of-scope values |
|---|---|---|---|
| upload body size | numeric (bytes) | `0 .. u64::MAX` | — |
| route | enum | `POST /api/v1/files` (multipart upload) | every other file-service route |
| `MAX_UPLOAD_BYTES` env override | numeric | any parseable `u64` | unparseable → falls back to `104_857_600` |

**Condition**

```rust
file_bytes.len() as u64 > config.server.max_upload_bytes   // handlers.rs:87 — strict >
```

Note the operator: the limit itself is **accepted**. That is the whole reason case B2 below exists.

**Expected outcome — per downstream system**

| System | Condition true (too large) | Condition false (accepted) |
|---|---|---|
| file-service HTTP response | `413`-class error `FileTooLarge { max_bytes, actual_bytes }` (`errors.rs:22`) | `201` with file metadata |
| S3 (`storage.rs`) | **no object written** | exactly one object written |
| DynamoDB metadata (`metadata.rs`) | **no item written** | exactly one item, `size_bytes` == body length |
| SNS `file.uploaded` event (`events.rs`) | **not published** | published once |
| audit-service | **no audit row** | one `file.uploaded` row |

**Boundary trio** (set `MAX_UPLOAD_BYTES` to a small test value, e.g. 1024, so the trio is cheap)

| # | Body size | Expected |
|---|---|---|
| B1 | `L - 1` (1023 B) | accepted, all four downstream effects present |
| B2 | `L` (1024 B) | **accepted** — pins the strict `>`; if this ever flips to rejected, the operator changed |
| B3 | `L + 1` (1025 B) | rejected, **zero** downstream effects |

**Additional size cases**

| # | Body size | Expected |
|---|---|---|
| B4 | `0` (zero-byte file) | documented behavior, asserted — not left to chance |
| B5 | multipart part absent entirely | `400`, not `500`, and no partial write |

**Scope negatives** — the size rule must not govern anything else

| # | Varied dimension | Value | Expected |
|---|---|---|---|
| N1 | route | `PUT /api/v1/files/{id}/rename` with a huge name payload | rule inert; rename is not size-gated by `MAX_UPLOAD_BYTES` |
| N2 | route | `POST /api/v1/folders` | rule inert |
| N3 | env override | `MAX_UPLOAD_BYTES="not-a-number"` | falls back to `104_857_600` (`config.rs:52`), does not panic, does not become `0` |
| N4 | combination | valid multipart, `L+1` bytes, but the request is unauthenticated | rejected for **auth**, and the size error is not what the client sees — proves ordering of the checks |

**Open questions** — none for this rule; the operator and the fallback are both explicit in source.

### A second, contrasting example (why you always copy the operator)

`ADMIN-QUOTA-001` (`services/admin-service/app/models/storage_quota.rb:27`) is the same *shape* as
`FS-UPLOAD-001` and the **opposite** edge:

```ruby
used_bytes >= quota_bytes    # non-strict — AT the limit you are already over quota
```

Two byte-count thresholds, two different answers at `limit`. This is exactly why the trio's middle
case is mandatory and why the rule record demands the predicate be copied from source rather than
paraphrased in English.

---

## 6. Open questions

Where the specification does not answer a case, do not guess and do not silently pick a behavior.
Record it under an **"Open questions"** heading in the rule record, phrased as a question with the
candidate answers, and — if the code today has *some* behavior — pin that behavior with a test
named `..._current_behavior_pending_decision` so a deliberate change turns it red.

If the unspecified case involves a **failure mode of a dependency** (timeout, 5xx, unavailable),
the question is always the same and always needs an explicit answer: **fail-open or fail-closed?**

---

## 7. Threshold register — every numeric threshold in the repo

Below is the repo-wide audit required by WP-13. Every row is a numeric constant that a comparison
or validation decides on, with the file:line that defines it and the work package that owns its
boundary trio. Work-package ids refer to `docs/TEST-COVERAGE-EXPANSION-SOW.md` §5.

Method: full-tree scan of `services/`, `frontend/`, `etl/`, `demo-platform/`, `clients/`,
`infrastructure/`, and `observability/` for numeric literals bound to comparison, validation,
clamp, pagination, retention, TTL and retry constructs, then manual triage to drop pure
presentation numbers (colours, font sizes, port numbers, HTTP status codes) and constants with no
decision attached.

Work-package ids below reference `docs/TEST-COVERAGE-EXPANSION-SOW.md`, which lands with WP-00; if
it is not yet on `main`, the ids are still stable and match the SOW's §5 table.

### 7.1 file-service (Rust) — WP-01 / WP-02

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| `MAX_UPLOAD_BYTES` | 104,857,600 (100 MB) | `services/file-service/src/config.rs:49-52` | `>` → reject upload (`handlers.rs:87`) | WP-01 (`FS-UPLOAD-001`) |
| `PORT` fallback | 8082 | `services/file-service/src/config.rs:45-48` | config default only | WP-02 (parse-failure negative) |
| list-files `page_size` clamp | default 50, max 100 | `services/file-service/src/handlers.rs:247` | `.min(100)` clamp | WP-01 |
| list-shared `page_size` clamp | default 50, max 100 | `services/file-service/src/handlers.rs:292` | `.min(100)` clamp | WP-01 |
| list-trashed `page_size` clamp | default 50, max 100 | `services/file-service/src/handlers.rs:318` | `.min(100)` clamp | WP-02 |
| `page` floor | 1 | `services/file-service/src/handlers.rs:246,291,317` | `.max(1)` clamp — `page=0` / negative | WP-01 |
| activity `limit` clamp | default 20, max 50 | `services/file-service/src/handlers.rs:657`, truncate at `:705` | `.min(50)` clamp | WP-01 |
| presigned download TTL | 3,600 s | `services/file-service/src/handlers.rs:366,370` | URL expiry | WP-01 |
| latency histogram buckets | 0.005 … 5.0 s | `services/file-service/src/middleware.rs:24` | metric bucketing (no behavior) | WP-02 (assert bucket set is stable) |

### 7.2 api-gateway (Go) — WP-03 / WP-04

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| `RATE_LIMIT_RPS` | 100 | `services/api-gateway/internal/config/config.go:75` | token-bucket capacity + refill rate | WP-04 (`GW-RATELIMIT-001`) |
| token-bucket admission | `tokens >= 1` | `services/api-gateway/internal/middleware/ratelimit.go:62` | allow vs `429` | WP-04 |
| bucket cleanup interval | 5 min | `services/api-gateway/internal/middleware/ratelimit.go:97` | GC tick (inject `now`, never sleep) | WP-04 |
| stale-bucket eviction | 10 min | `services/api-gateway/internal/middleware/ratelimit.go:103` | `>` → delete bucket | WP-04 |
| `CORS_MAX_AGE` | 300 s | `services/api-gateway/internal/config/config.go:82` | preflight cache header | WP-03 |
| `SHUTDOWN_TIMEOUT_SECONDS` | 30 s | `services/api-gateway/internal/config/config.go:84` | graceful-shutdown deadline | WP-03 |
| `CB_MAX_REQUESTS` | 5 | `services/api-gateway/internal/config/config.go:86` | half-open probe cap | WP-04 |
| `CB_INTERVAL_SECONDS` | 60 s | `services/api-gateway/internal/config/config.go:87` | closed-state count reset cycle | WP-04 |
| `CB_TIMEOUT_SECONDS` | 30 s | `services/api-gateway/internal/config/config.go:88` | open → half-open transition | WP-04 |
| `CB_FAILURE_RATIO` | 0.6 | `services/api-gateway/internal/config/config.go:89` | `>=` ratio trips breaker — express trio in counts | WP-04 |

### 7.3 auth-service (Java) — WP-05

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| access-token TTL | 3,600 s | `services/auth-service/src/main/java/com/otterworks/auth/config/JwtConfig.java:14`, `application.yml:33` | `exp` claim → 401 after | WP-05 (`AUTH-TOKEN-001`) |
| refresh-token TTL | 2,592,000 s (30 d) | `.../config/JwtConfig.java:15`, `application.yml:34` | `expiresAt` check `AuthService.java:88` | WP-05 |
| password length | min 8, max 128 | `.../dto/RegisterRequest.java:13`, `.../dto/ChangePasswordRequest.java:12` | validation reject | WP-05 |
| display name length | min 1, max 100 | `.../dto/RegisterRequest.java:17`, `.../dto/UpdateProfileRequest.java:8` | validation reject | WP-05 |
| bio / avatar length | max 500 | `.../dto/UpdateProfileRequest.java:11` | validation reject | WP-05 |
| email column length | 255 | `.../entity/User.java:23` | persistence truncation/failure | WP-05 |
| `token_id` column length | 255 | `.../entity/RefreshToken.java:25` | persistence | WP-05 |
| role name length | 50 | `.../entity/Role.java:19` | persistence | WP-05 |
| settings enum columns | 10 | `.../entity/UserSettings.java:34,37` | persistence | WP-05 |

### 7.4 document-service (Python) — WP-06

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| search `page` floor | `ge=1` | `services/document-service/app/api/documents.py:152` | 422 below | WP-06 |
| search `size` bounds | default 20, `ge=1, le=100` | `services/document-service/app/api/documents.py:153` | 422 outside | WP-06 (`DOC-PAGE-001`) |
| list `page` / `size` bounds | same | `services/document-service/app/api/documents.py:195-196` | 422 outside | WP-06 |
| versions `page` / `size` bounds | same | `services/document-service/app/api/documents.py:213-214` | 422 outside | WP-06 |
| query `q` min length | 1 | `services/document-service/app/api/documents.py:151` | 422 on empty | WP-06 |
| title length | 1 … 500 | `services/document-service/app/schemas/document.py:12,22,31,128` | 422 outside | WP-06 |
| template name length | 1 … 500 | `services/document-service/app/schemas/document.py:107` | 422 outside | WP-06 |
| comment content min length | 1 | `services/document-service/app/schemas/document.py:89` | 422 on empty | WP-06 |
| recent-documents cap | 5 | `services/document-service/app/services/document_service.py:114` | `.limit(5)` | WP-06 |
| `db_pool_size` | 10 | `services/document-service/app/config.py:14` | pool sizing | WP-06 (config-parse test only) |
| `db_max_overflow` | 20 | `services/document-service/app/config.py:15` | pool sizing | WP-06 (config-parse test only) |
| redis `socket_timeout` | 1 s | `services/document-service/app/api/documents.py:40` | chaos-flag lookup timeout | WP-06 |

### 7.5 search-service (Python) — WP-07

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| GET search `page` floor | 1 | `services/search-service/app/api/search.py:54` | `max(1, …)` clamp | WP-07 |
| GET search `size` clamp | default 20, 1 … 100 | `services/search-service/app/api/search.py:55` | clamp, `400` on non-numeric (`:57`) | WP-07 (`SRCH-PAGE-001`) |
| POST search `page` floor | 1 | `services/search-service/app/api/search.py:135` | clamp | WP-07 |
| POST search `size` clamp | default 20, 1 … 100 | `services/search-service/app/api/search.py:136` | clamp | WP-07 |
| `MAX_ANALYTICS_ENTRIES` | 10,000 | `services/search-service/app/services/meilisearch_client.py:28`, trim at `:39-40` | `>` → trim ring buffer | WP-07 |
| indexer fetch page size | 100 | `services/search-service/app/services/indexer.py:114,150` | crawl batch | WP-07 |
| `FETCH_TIMEOUT` | 30 s | `services/search-service/app/services/indexer.py:16` | HTTP timeout | WP-07 |
| Meili task wait | 10,000 / 30,000 / 60,000 ms | `.../meilisearch_client.py:273,92,343,355,362` | task-wait deadline | WP-07 |
| SQS `max_messages` | 10 | `services/search-service/app/config.py:28` | receive batch size | WP-07 |
| SQS `wait_time_seconds` | 20 | `services/search-service/app/config.py:29` | long-poll window | WP-07 |
| SQS `visibility_timeout` | 60 s | `services/search-service/app/config.py:30` | redelivery window | WP-07 |
| consumer thread join | 5 s | `services/search-service/app/services/sqs_consumer.py:58` | shutdown deadline | WP-07 |

### 7.6 notification-service (Kotlin) — WP-08

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| `page` default | 1 | `.../notification/routes/Routes.kt:61` | **unvalidated** — `page=0` / negative reach the repository | WP-08 |
| `page_size` default | 20 | `.../notification/routes/Routes.kt:62` | **unclamped** — no upper bound (contrast every other service) | WP-08 (`NOTIF-PAGE-001`) |
| repository paging defaults | page 1, pageSize 20 | `.../notification/repository/NotificationRepository.kt:61-62` | slice offsets `:87-88` | WP-08 |
| mark-all-read page size | `Int.MAX_VALUE` | `.../notification/repository/NotificationRepository.kt:144` | unbounded fetch | WP-08 |
| `SQS_MAX_MESSAGES` | 10 | `.../notification/config/AppConfig.kt:33` | receive batch size | WP-08 |
| WS ping period | 30,000 ms | `.../notification/Application.kt:74` | keepalive | WP-08 |
| WS timeout | 15,000 ms | `.../notification/Application.kt:75` | disconnect deadline | WP-08 |
| WS max frame size | `Long.MAX_VALUE` | `.../notification/Application.kt:76` | **no frame cap** — flag as a finding, do not change | WP-08 |

### 7.7 audit-service (C#) — WP-09

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| `pageSize` clamp | default 20, 1 … 100 | `services/audit-service/src/Controllers/AuditController.cs:75` | `Math.Clamp` | WP-09 (`AUDIT-PAGE-001`) |
| `ArchiveAfterDays` | 90 | `services/audit-service/src/Config/AwsSettings.cs:10` | archive cutoff `AuditService.cs:151` | WP-09 |
| suspicious-activity threshold | `max(avg × 3, 100)` | `services/audit-service/src/Services/AuditService.cs:114` | flags a user as suspicious | WP-09 (`AUDIT-SUSPECT-001`) |
| export windows | 1 / 7 / 30 / 90 / 365 d | `services/audit-service/src/Services/AuditService.cs:160-165` | window selection, default 30 d on unknown token | WP-09 |
| DynamoDB batch size | 25 | `services/audit-service/src/Services/DynamoDbAuditRepository.cs:269` | write-batch chunking, boundary at 24/25/26 items | WP-09 |
| SNS receive batch | 10 | `services/audit-service/src/Services/SnsConsumer.cs:53` | receive batch size | WP-09 |
| SNS long-poll wait | 20 s | `services/audit-service/src/Services/SnsConsumer.cs:54` | long-poll window | WP-09 |

### 7.8 admin-service (Ruby) — WP-10

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| over-quota predicate | `used_bytes >= quota_bytes` | `services/admin-service/app/models/storage_quota.rb:27`, scope `:17` | **non-strict** — at the limit you are over | WP-10 (`ADMIN-QUOTA-001`) |
| `quota_bytes` validity | `> 0` | `services/admin-service/app/models/storage_quota.rb:13` | rejects 0 and negative | WP-10 |
| `used_bytes` validity | `>= 0` | `services/admin-service/app/models/storage_quota.rb:14` | rejects negative | WP-10 |
| tier limits | 5 GB / 50 GB / 200 GB / 1 TB | `services/admin-service/app/models/storage_quota.rb:5-10` | tier → quota mapping | WP-10 |
| `rollout_percentage` range | 0 … 100 | `services/admin-service/app/models/feature_flag.rb:4-8` | validation reject outside | WP-10 (`ADMIN-FLAG-001`) |
| rollout bucketing | `md5(name:user) % 100 < pct` | `services/admin-service/app/models/feature_flag.rb:24` | `<` — pct 0 never fires, pct 100 short-circuits at `:22` | WP-10 |
| `per_page` clamp | default 20, 1 … 100 | `services/admin-service/app/controllers/application_controller.rb:44` | double clamp (`.min(100).clamp(1,100)`) | WP-10 |
| `page` floor | 1 | `services/admin-service/app/controllers/application_controller.rb:43` | `.max` clamp | WP-10 |
| announcement title length | 255 | `services/admin-service/app/models/announcement.rb:5` | validation reject | WP-10 |
| incident title length | 255 | `services/admin-service/app/models/incident.rb:17` | validation reject | WP-10 |
| admin display-name length | 255 | `services/admin-service/app/models/admin_user.rb:9` | validation reject | WP-10 |
| recent-signups window | 30 d | `services/admin-service/app/services/metrics_aggregator.rb:19` | metric window (inject clock) | WP-10 |
| audit metrics window | 7 d | `services/admin-service/app/services/metrics_aggregator.rb:53-54` | metric window (inject clock) | WP-10 |
| top-actions cap | 5 | `services/admin-service/app/services/metrics_aggregator.rb:54` | `.limit(5)` | WP-10 |

### 7.9 collab-service (Node) — WP-11

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| `PERSIST_INTERVAL_MS` | 30,000 | `services/collab-service/src/config.ts:52` (default also at `handlers/collaboration.ts:577`) | persist tick | WP-11 |
| `SNAPSHOT_INTERVAL_MS` | 300,000 | `services/collab-service/src/config.ts:53` (also `handlers/collaboration.ts:578`) | snapshot tick | WP-11 |
| `DOC_TTL_SECONDS` | 86,400 | `services/collab-service/src/config.ts:54`, applied `services/document-store.ts:44,60,77` | Redis key expiry | WP-11 |
| `SNAPSHOT_TTL_SECONDS` | 604,800 | `services/collab-service/src/config.ts:55`, applied `services/document-store.ts:45,125` | Redis key expiry | WP-11 |
| `MAX_SNAPSHOTS` | 50 | `services/collab-service/src/config.ts:56`, trim `services/document-store.ts:121-122` | `>` → `ltrim` to N | WP-11 (`COLLAB-SNAP-001`) |
| `getSnapshots` default limit | 20 | `services/collab-service/src/services/document-store.ts:135` | `lrange 0, limit-1` | WP-11 |
| awareness `maxIdleMs` | caller-supplied | `services/collab-service/src/services/awareness.ts:207,214` | `>` → drop awareness state | WP-11 |
| Redis `maxRetriesPerRequest` | 3 | `services/collab-service/src/services/redis-adapter.ts:27` | retry cap | WP-11 |
| Redis retry backoff cap | `min(times × 200, 5000)` ms | `services/collab-service/src/services/redis-adapter.ts:28` | backoff ceiling | WP-11 |

### 7.10 analytics-service (Scala) — WP-12

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| top-content `limit` default | 10 | `.../analytics/api/AnalyticsRoutes.scala:66`, `.../service/AnalyticsService.scala:46` | `.take(limit)` `MetricsAggregator.scala:101` | WP-12 |
| recent-activity cap | 20 | `.../analytics/repository/MetricsAggregator.scala:43` | `.take(20)` | WP-12 |
| DB executor queue size | 1,000 | `.../analytics/db/AnalyticsDb.scala:35` | Slick queue capacity | WP-12 |
| observation value validity | `<= 0` rejected | `.../analytics/service/MarginService.scala:114` | itemized rejection | WP-12 (`ANLY-INGEST-001`) |
| zero list price guard | `== 0` | `.../analytics/service/MarginService.scala:44` | margin → 0 instead of divide-by-zero | WP-12 |

### 7.11 report-service (Java 8) — WP-12

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| `otterworks.report.max-rows` | 50,000 | `services/report-service/src/main/resources/application.properties:38`, `.../config/AppConfig.java:39-40` | `>` → truncate (`ReportGenerationWorker.java:92-94`) | WP-12 (`RPT-ROWS-001`) |
| fetcher cache max size | 100 | `.../report/service/ReportDataFetcher.java:52` | Caffeine eviction | WP-12 |
| fetcher cache TTL | 5 min | `.../report/service/ReportDataFetcher.java:53` | `expireAfterWrite` | WP-12 |

### 7.12 legacy-portal (Java 11) — WP-12

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| feedback rating range | 1 … 5 | `.../legacyportal/feedback/FeedbackService.java:10-11`, check at `:21` | throws outside | WP-12 (`PORTAL-RATING-001`) |
| rating bean validation | `@Max(5)` | `.../legacyportal/feedback/FeedbackController.java:56` | 400 outside — note: **no `@Min`** on the controller, only the service guard | WP-12 |
| feedback subject length | 100 | `.../legacyportal/feedback/FeedbackController.java:52` | validation reject | WP-12 |
| feedback message length | 2,000 | `.../feedback/FeedbackController.java:60`, column `Feedback.java:28` | validation reject | WP-12 |
| announcement title length | 200 | `.../announcements/AnnouncementController.java:57`, column `Announcement.java:22` | validation reject | WP-12 |
| announcement body length | 4,000 | `.../announcements/AnnouncementController.java:61`, column `Announcement.java:25` | validation reject | WP-12 |
| preference key/value length | 20 | `.../userpreferences/UserPreferenceController.java:42,46` | validation reject | WP-12 |

### 7.13 ETL cron jobs (Python) — WP-20

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| audit retention | 90 d | `etl/scripts/audit_archive_weekly.py:47` | archive cutoff | WP-20 (`ETL-ARCHIVE-001`) |
| DynamoDB batch size | 25 | `etl/scripts/audit_archive_weekly.py:49` | batch-write chunking | WP-20 |
| activity lookback | 30 d | `etl/scripts/user_activity_daily.py:42` | aggregation window | WP-20 |
| reindex bulk batch | 500 | `etl/scripts/search_reindex_weekly.py:35` | bulk chunking | WP-20 |
| reindex API page size | 100 | `etl/scripts/search_reindex_weekly.py:36` | crawl paging | WP-20 |
| analytics `max_messages` | 10,000 | `etl/scripts/analytics_daily.py:62` | hard cap on drain | WP-20 |
| analytics batch size | 10 | `etl/scripts/analytics_daily.py:63` | receive batch | WP-20 |
| consecutive-error abort | 3 | `etl/scripts/analytics_daily.py:82` | `>=` → abort run | WP-20 (`ETL-ABORT-001`) |
| orphan-report trigger | `> 0` orphans | `etl/scripts/storage_cleanup_daily.py:153` | emit report | WP-20 |
| savings cost factor | 0.023 USD/GB | `etl/scripts/storage_cleanup_daily.py:162` | reported estimate | WP-20 |

### 7.14 demo-platform — WP-21

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| `IDLE_AFTER_SECONDS` | 3,600 | `demo-platform/reaper/idle-suspend.sh:47`, compared `:250` | `<` → not idle; `>=` → scale to zero | WP-21 (`PLAT-IDLE-001`) |
| reaper `grace_seconds` | 0 (reaper default) | `demo-platform/reaper/reaper.sh:401`, compared `:236` | `expires_at + grace < now` → GC tenant | WP-21 (`PLAT-TTL-001`) |
| dashboard `graceSeconds` default | 300 | `demo-platform/dashboard/lib/control.ts:287` | **disagrees with the reaper's own default of 0** — see finding note below | WP-21 |
| dashboard `idleAfterSeconds` default | 3,600 | `demo-platform/dashboard/lib/control.ts:291` | matches reaper | WP-21 |
| checkout lock TTL | 15 min (`15 * 60`) | `demo-platform/dashboard/lib/control.ts:140` | lock auto-expiry | WP-21 |
| `NEVER_TTL_SECONDS` | 315,360,000 (10 y) | `demo-platform/dashboard/lib/util.ts:21` | `>=` → treated as perpetual (`extend/route.ts:21`, `persist/route.ts:43`) | WP-21 |
| TTL parse bounds | `num > 0`, units m/h/d | `demo-platform/dashboard/lib/util.ts:31-45` | `null` → `400 invalid ttl` | WP-21 |
| unpersist default TTL | `"24h"` | `demo-platform/dashboard/app/api/tenants/[id]/persist/route.ts:14` | fallback | WP-21 |
| reaper job `ttlSecondsAfterFinished` | 120 | `demo-platform/reaper/reaper.sh:283` | k8s job GC | WP-21 |
| reaper job wait timeout | 90 s | `demo-platform/reaper/reaper.sh:302` | `kubectl wait` deadline | WP-21 |
| RDS connect timeout | 10 s | `demo-platform/reaper/reaper.sh:296` | psql connect deadline | WP-21 |
| reaper schedule | `*/15 * * * *` | `demo-platform/dashboard/lib/control.ts:286` | sweep cadence | WP-21 |

### 7.15 frontends — WP-14 / WP-16

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| `MAX_PREVIEW_SIZE` | 500,000 B | `frontend/client-app/src/components/files/file-preview.tsx:4`, range header `:30` | truncate preview | WP-14 (`WEB-PREVIEW-001`) |
| files list default page size | 50 | `frontend/client-app/src/lib/api.ts:162` | request paging | WP-14 |
| shared / trashed default page size | 50 | `frontend/client-app/src/lib/api.ts:282,295` | request paging | WP-14 |
| documents default page size | 50 | `frontend/client-app/src/lib/api.ts:331` | request paging | WP-14 |
| notifications default page size | 20 | `frontend/client-app/src/lib/api.ts:408`, response fallback `:381` | request paging | WP-14 |
| `PAGE_LIMIT` (drain loop) | 100 | `frontend/client-app/src/lib/api.ts:474` | pagination crawl chunk | WP-14 |
| trash page size | 50 | `frontend/client-app/src/pages/trash.tsx:72` | request paging | WP-14 |
| quota paginator options | 5 / 10 / 25 | `frontend/admin-dashboard/src/app/pages/quotas/quotas.component.ts:103` | table paging | WP-16 |
| GB divisor | 1024³ | `frontend/admin-dashboard/src/app/pages/quotas/quotas.component.ts:140,165` | unit conversion | WP-16 |

### 7.16 clients/windows-desktop — WP-22

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| HTTP client timeout | 30 s | `clients/windows-desktop/OtterWorks.Desktop/Services/OtterWorksApiClient.cs:33` | request deadline | WP-22 |

### 7.17 infrastructure + observability — WP-23

| Threshold | Value | Defined at | Decision | Trio owner |
|---|---|---|---|---|
| S3 noncurrent-version transition | 30 d | `infrastructure/terraform/modules/storage/main.tf:55` | lifecycle transition | WP-23 |
| S3 noncurrent-version expiration | 365 d | `infrastructure/terraform/modules/storage/main.tf:59` | lifecycle deletion | WP-23 |
| SQS message retention (main) | 86,400 s | `infrastructure/terraform/modules/messaging/main.tf:28,68` | queue retention | WP-23 |
| DLQ message retention | 1,209,600 s (14 d) | `infrastructure/terraform/modules/messaging/main.tf:43` | DLQ retention | WP-23 |
| secondary queue retention | 259,200 s (3 d) | `infrastructure/terraform/modules/messaging/main.tf:55` | queue retention | WP-23 |
| container default CPU / memory | 500m / 256Mi | `infrastructure/k8s/limitrange.yaml` (`spec.limits[0].default`) | admission default | WP-23 |
| container default requests | 100m / 128Mi | `infrastructure/k8s/limitrange.yaml` (`spec.limits[0].defaultRequest`) | admission default | WP-23 |
| namespace quota | 2 CPU / 4Gi / 20 pods | `infrastructure/k8s/resourcequota.yaml` (`spec.hard`) | admission rejection at the cap | WP-23 (`IAC-QUOTA-001`) |
| `ServiceDown` for-duration | 1 m | `observability/prometheus/alerts.yml:9` | alert fires | WP-23 |
| restart-churn threshold | `> 2` in 15 m, for 5 m | `observability/prometheus/alerts.yml:20-21` | alert fires | WP-23 |
| error-rate warning | `> 0.05`, for 2 m | `observability/prometheus/alerts.yml:41-42` | alert fires | WP-23 |
| error-rate critical | `> 0.25`, for 5 m | `observability/prometheus/alerts.yml:58-59` | alert fires | WP-23 |
| latency p-threshold (warning) | `> 2`, for 5 m | `observability/prometheus/alerts.yml:77-78` | alert fires | WP-23 |
| latency p-threshold (secondary) | `> 1`, for 5 m | `observability/prometheus/alerts.yml:93-94` | alert fires | WP-23 |
| saturation warning | `> 0.80`, for 5 m | `observability/prometheus/alerts.yml:114-115` | alert fires | WP-23 |
| saturation critical | `> 0.90`, for 2 m | `observability/prometheus/alerts.yml:132-133` | alert fires | WP-23 |
| CPU alert | `> 0.80`, for 5 m | `observability/prometheus/alerts.yml:145-146` | alert fires | WP-23 |
| SQS backlog alert | `> 1000` visible | `observability/prometheus/alerts.yml:194` | alert fires | WP-23 |

### 7.18 Not in this register

Deliberately excluded, with the reason:

- **Ports** (8080-8091, 3000, 4200, 5432, 7700) — addresses, not decisions.
- **HTTP status codes** — outcomes, not thresholds.
- **CSS/px/colour values** in `frontend/**` and `clients/windows-desktop/**/*.xaml` — presentation.
- **Snackbar durations** (3,000 ms, e.g. `frontend/admin-dashboard/src/app/pages/quotas/quotas.component.ts:177`) — UX timing with no branch.
- **Fixture magnitudes** in `services/report-service/.../ReportDataFetcher.java:159,178,196` (50/50/25 synthetic rows) — test data generation, not a rule.
- **Version strings** (`0.1.0`) and **schema versions**.

If you add a numeric constant that a comparison decides on, add a row here in the same PR.

### 7.19 Findings surfaced by the audit (do not fix — document)

These were observed while compiling §7. Per `AGENTS.md`, `main` is the golden app and may contain
deliberately planted bugs; nothing here was changed.

1. **Reaper grace default disagrees across the two implementations.** `reaper.sh:401` defaults
   `grace_seconds` to `0`; the dashboard's `DEFAULT_REAPER` (`control.ts:287`) shows `300`. The
   dashboard's own comment block (`control.ts:282-284`) states the intent that defaults must mirror
   the reaper. An operator reading the dashboard sees a 5-minute grace that the reaper is not
   applying. **Judged: genuine, low severity, unverified against a planted-bug list.** Owner: WP-21.
2. **notification-service does not bound `page_size`.** `Routes.kt:62` parses `page_size` with no
   upper clamp, unlike file-service (`.min(100)`), admin-service (`clamp(1,100)`),
   audit-service (`Math.Clamp(…,1,100)`), document-service (`le=100`) and search-service
   (`min(100,…)`). `page`/`page_size` of `0` or negative also reach the repository slice
   (`NotificationRepository.kt:87-88`). **Judged: genuine gap, plausibly the same area as the
   documented live `400` bug in `docs/exploratory-qa-report.md`; not verified as planted.**
   Owner: WP-08 — pin current behavior, do not change it.
3. **legacy-portal rating has `@Max(5)` but no `@Min`** on the controller DTO
   (`FeedbackController.java:56`); the lower bound exists only in the service guard
   (`FeedbackService.java:21`). The two layers therefore reject `0` with different mechanisms and
   different responses. **Judged: genuine inconsistency, cannot determine planted vs. incidental.**
   Owner: WP-12 — assert both paths, change neither.
4. **`maxFrameSize = Long.MAX_VALUE`** on the notification WebSocket (`Application.kt:76`) means no
   frame-size ceiling. **Judged: intentional-looking configuration, not a bug per se**, but it is an
   unbounded input and belongs in WP-08's negative cases as documented behavior.

---

## 8. Determinism rules for decision-table tests

Binding for every package in this program:

1. **No `sleep`.** Time-dependent thresholds (TTLs, refill windows, idle windows, cache expiry)
   must be driven by an injected clock. The gateway already provides one
   (`RateLimiter.now`, `ratelimit.go:24`; `CircuitBreaker.now`, `circuitbreaker.go:50`) — use it,
   and add the equivalent seam in a *test* rather than changing production code where one is absent.
2. **No wall-clock dependence.** A test that behaves differently at 23:59 UTC, on a leap day, or
   across a DST transition is a failing test that has not failed yet.
3. **No inter-test ordering.** Every case constructs its own fixtures. Run the suite in random order
   where the framework supports it; run it twice before opening the PR.
4. **No shared mutable fixtures.** Two cases in the same table must not share a mutable object,
   a database row, a Redis key, or a module-level cache.
5. **Deterministic identities.** Where a rule keys on an identity or a hash (e.g. the feature-flag
   bucketing at `feature_flag.rb:23`), pick fixed inputs and assert the computed bucket, so the case
   cannot flake on a different hash.

## 9. Interaction with the golden-app policy

`main` is the golden app and contains deliberately planted bugs (`AGENTS.md`). When a decision-table
case is red because the product is wrong:

- **Do not fix the product.** Keep the case, marked skipped / expected-fail
  (`#[ignore]`, `@Disabled`, `pytest.mark.xfail`, `it.skip`, `Ignore = "…"`), with a comment naming
  the defect and the rule id.
- **Do not weaken or delete an existing test** to make a new one pass.
- If you cannot tell whether the defect is planted or genuine, say so in the PR and change nothing.

## 10. Reviewer checklist

A decision-table PR is rejected unless all of the following hold.

- [ ] Every rule touched has a rule record with a stable `<AREA>-<RULE>-<NNN>` id.
- [ ] The condition is **copied from source**, operator included, with a file:line reference.
- [ ] The boundary trio exists: `limit-1`, `limit`, `limit+1`. The `limit` case is present.
- [ ] Zero / absent / invalid cases exist for size and count inputs.
- [ ] Outcomes are asserted **per downstream system**, including at least one system asserted to be
      untouched.
- [ ] One scope negative per out-of-scope dimension, each varying exactly one dimension, plus one
      combination negative.
- [ ] Scope negatives assert *no side effect*, not merely a 2xx.
- [ ] No `sleep`, no wall-clock dependence, no ordering dependence, no shared mutable fixtures.
- [ ] Suite passes twice, in random order where supported.
- [ ] No production-code change; no existing test edited or deleted; no planted bug "fixed".
- [ ] Any new numeric threshold added a row to §7.

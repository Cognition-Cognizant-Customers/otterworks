# Decision-Table Testing Standard (OtterWorks house style)

**Status:** normative for any work package that touches a rule with a numeric threshold or a
per-category outcome. **Owner:** QE. **Companion worked example:**
[`brd-credit-decline-matrix.md`](./brd-credit-decline-matrix.md).

A *decision table* is a rule of the shape "**when** these input dimensions hold **and** this
numeric condition is true, **then** these outcomes occur **in each of these systems**". Most of
OtterWorks' business logic is exactly this shape — upload size limits, storage quotas, rate limits,
pagination caps, token TTLs, tenant TTLs — and it is exactly where the repo's tests are thinnest
(the coverage inventory found positive-path assertions almost everywhere and `limit±1` assertions
almost nowhere).

This document defines the minimum a test suite must contain for such a rule. It is deliberately
short: fill in the table, write the trio, write the scope negatives, tick the checklist.

---

## 1. Why this shape, and the two failure modes it catches

**Failure mode 1 — `<` vs `<=`.** An off-by-one in a threshold comparison is invisible to any test
that only exercises "clearly under" and "clearly over". It is caught only by asserting the value
*at* the limit. Every threshold therefore gets a **boundary trio**: `limit-1`, `limit`, `limit+1`.

**Failure mode 2 — one system agreed, the other did not.** A rule almost never has a single
outcome. It has an outcome *per downstream system*: the request is rejected **and** an event is
published **and** an audit row is written **and** a notification is generated. "Rejected by service
A" and "recorded in service B" are two separate assertions that can, and do, disagree. A decision
table that collapses them into one "expected result" column cannot express the bug where the API
returns 413 but no audit event is ever emitted.

The second insight is borrowed from the credit-decline BRD transcribed in the companion document,
where "declination notice **sent** by the Agency Portal" and "declination notice **generated** in
PolicyCenter" are distinct, independently-observable outcomes, and the interesting defects live in
the gap between them.

---

## 2. The template

Copy this block into the test file's header comment, or into the WP's design note, before writing
any code. Every field is mandatory; write "n/a — <reason>" rather than leaving one blank.

```
Rule id:        <SERVICE>-<SHORT-NAME>-<n>          e.g. FILE-UPLOAD-SIZE-1
Source:         <file:line of the constant> + <file:line of the comparison>
Owning WP:      <WP-NN>

Dimensions (the inputs that select which rule applies):
  D1 <name>   values: <enumerated set>          scoping? yes/no
  D2 <name>   values: <enumerated set>          scoping? yes/no
  ...

Condition:      <the exact predicate, copied from the code, operator included>
                e.g. "file_bytes.len() as u64 > config.server.max_upload_bytes"
Threshold:      <constant name> = <value> (<unit>), configurable via <env var / setting>

Expected outcome, per downstream system (one row per observable system):
  S1 <API response>            -> <status + body assertion>
  S2 <persistence>             -> <row written / not written>
  S3 <event bus>               -> <event published / not published, with what payload>
  S4 <audit / notification>    -> <record generated / not generated>

Non-outcomes (what must NOT happen):
  <e.g. no partial object left in S3; no quota counter incremented>
```

### 2.1 Dimensions vs. scoping dimensions

A **dimension** is any input that participates in the rule. A **scoping dimension** is one whose
value determines whether the rule fires *at all* (policy type, tenant tier, HTTP route, user role,
entry point). Scoping dimensions drive the mandatory scope-negatives in §4. Mark each one.

### 2.2 One rule id per (rule, scope) pair

If the same conceptual rule has a different threshold per category — the BRD's HO4 at 580 and HO6
at 590; OtterWorks' `TIER_LIMITS` free/basic/pro/enterprise — that is **two or more rule ids**, each
with its own boundary trio, plus at least one **cross-scope** case proving the two thresholds are
not swapped (see §3.3).

---

## 3. Mandatory boundary trio

For every numeric threshold in the table, the suite must contain three cases:

| Case | Input | Asserts |
|---|---|---|
| `limit-1` | the largest value strictly on the permissive side | rule does **not** fire |
| `limit` | **the threshold value itself** | the operator (`<` vs `<=`, `>` vs `>=`) — this is the whole point |
| `limit+1` | the smallest value strictly on the restrictive side | rule **does** fire |

Rules:

1. **Name the test after the value, not the intent.** `rejects_at_max_upload_bytes_plus_one`, not
   `rejects_large_files`. A reader must be able to see the boundary in the test list.
2. **Assert every system in the table** for at least the `limit` and `limit+1` cases. A trio that
   only checks the HTTP status is half a trio.
3. **"1" is unit-dependent.** For bytes, ±1 byte. For a monetary or score value, ±1 in the
   smallest representable increment. For a time window, ±1 second *and* — if the code compares
   with second granularity — a case at exactly the boundary second.
4. **Non-integer domains need an extra case.** If the input can be fractional (a failure *ratio*,
   a percentage, a float score), add the nearest representable value below and above the limit, and
   state in the comment whether the domain is genuinely continuous or quantized.
5. **Do not compute the expected value from the constant.** Write `100 * 1024 * 1024 + 1` or the
   literal, not `MAX + 1` read from the same config the product reads — otherwise a wrong constant
   makes both sides wrong and the test passes.
6. **Clamps are two thresholds.** `Math.Clamp(size ?? 20, 1, 100)` and
   `[(params[:per_page] || 20).to_i, 100].min.clamp(1, 100)` each need a trio at the lower bound
   (0 / 1 / 2) *and* a trio at the upper bound (99 / 100 / 101), plus the default-when-absent case.
7. **Defaults are a case.** Omitting the input entirely must be asserted separately from passing
   the default value explicitly; they are different code paths.

### 3.1 Below/above the domain

Add, per threshold: the value `0`, a negative value, and a value beyond the domain maximum
(e.g. a credit score of `900`, a `page_size` of `2^31`). Each must have a **stated** expected
behavior — reject as invalid input, or clamp, or evaluate. "Whatever the code does today" is not an
answer; if the behavior is undefined, that is an open question for the BA (§6) and the test is
written skipped/xfail with the question in the comment.

### 3.2 Missing / malformed input

Per threshold: absent, `null`, empty string, and non-numeric string. State whether each is a hard
error or a fallback to the default.

### 3.3 Cross-scope proof (when the threshold varies by category)

Two extra cases per pair of scopes, chosen so that swapping the two thresholds in the product would
turn them red:

- a value that fires under scope A's threshold but not under scope B's, submitted **as scope B**;
- the mirror case.

Without these, a copy-paste that assigns the pro tier's limit to the free tier passes every trio.

---

## 4. Mandatory scope-negatives

A decision table describes when the rule *fires*. It is equally a description of when it must
**not** fire — and that half is almost never tested.

Required, at minimum:

1. **One negative per scoping dimension.** Hold everything else at a value that *would* fire
   (typically deep inside the restrictive side of the threshold, not at the boundary — you are
   testing scope, not the boundary) and vary exactly one scoping dimension to an out-of-scope
   value. Assert the rule does not fire **and** that none of the downstream systems recorded
   anything.
2. **One combination negative.** Every dimension in scope except one, which is out of scope. This
   catches an implementation that ORs its scope predicates where the spec ANDs them — the single
   most common scoping defect, and one that no single-dimension negative can see.
3. **One authorization negative wherever the code has an owner concept.** User A cannot trip,
   read, or reset user B's rule state (quota, share, document, notification, audit record, tenant).
   Assert the failure is a `403`/`404` *and* that B's state is unchanged.
4. **One idempotency case wherever the rule produces a side effect.** Submit the triggering
   request twice; assert exactly one event / notice / audit row. A rule that fires twice on a
   retry is a duplicate-notification defect.

Every negative asserts the **absence** of each downstream outcome, not just the primary response.
"No notice was sent" is a real assertion with a real query behind it.

---

## 5. Worked example — how to fill this in

Real OtterWorks rule, written out in full. The trio does not exist in the suite today; WP-01 owns
writing it.

```
Rule id:        FILE-UPLOAD-SIZE-1
Source:         constant  services/file-service/src/config.rs:49  (MAX_UPLOAD_BYTES, default 104857600)
                predicate services/file-service/src/handlers.rs:87
Owning WP:      WP-01

Dimensions:
  D1 request body size (bytes)   values: 0 .. unbounded            scoping? no
  D2 route                       values: POST /files (upload)      scoping? yes
  D3 authenticated owner         values: owner | other user | none scoping? yes

Condition:      file_bytes.len() as u64 > config.server.max_upload_bytes
                => the limit value itself is ACCEPTED (strict >, not >=)
Threshold:      max_upload_bytes = 104_857_600 (100 MiB), env MAX_UPLOAD_BYTES

Expected outcome, per downstream system:
  S1 HTTP response      -> 413 FileTooLarge { max_bytes, actual_bytes } when it fires; 201 otherwise
  S2 S3 object          -> no object written when it fires
  S3 DynamoDB metadata  -> no FileMetadata row when it fires
  S4 event bus          -> no file.created event when it fires; exactly one when it does not

Non-outcomes:
  no partially-streamed object left in the bucket; no version row; owner's used_bytes unchanged
```

Cases this generates:

| # | Case | Input | Expected |
|---|---|---|---|
| 1 | `accepts_at_max_upload_bytes_minus_one` | 104,857,599 B | 201; object + metadata + event present |
| 2 | `accepts_at_exactly_max_upload_bytes` | **104,857,600 B** | 201 (proves `>` not `>=`) |
| 3 | `rejects_at_max_upload_bytes_plus_one` | 104,857,601 B | 413; **no** object, **no** metadata, **no** event |
| 4 | `accepts_zero_byte_file` | 0 B | stated behavior (201 today) |
| 5 | `rejects_missing_multipart_field` | no file part | 400; nothing written |
| 6 | `honors_env_override` | `MAX_UPLOAD_BYTES=10`, 11 B | 413 — the constant is read, not hard-coded |
| 7 | `other_user_cannot_upload_into_owners_folder` | authz negative | 403; nothing written |
| 8 | `duplicate_upload_of_same_bytes_is_idempotent_or_versioned` | same file twice | exactly one of the stated outcome |

Eight cases for one integer. That is the expected cost, and it is why the trio is mandatory rather
than aspirational: a threshold nobody is willing to spend eight cases on is a threshold nobody
should be shipping.

---

## 6. Open questions belong in the document, not in the code

When the specification does not answer "what should happen at/over/outside the boundary", do
**not** guess and encode today's behavior as if it were intended. Write the case as
skipped/expected-fail, with a comment naming the question and who must answer it, and list it in
an **"Open questions"** section of the WP's doc (see the companion document's §5 for the format).
Each open question becomes an active test the day it is answered.

---

## 7. Reviewer checklist

A PR that touches a threshold rule is not approvable until every box is ticked.

- [ ] The rule template (§2) is filled in, with a `file:line` for both the constant **and** the
      comparison operator.
- [ ] The **operator** is recorded explicitly (`<` / `<=` / `>` / `>=`) and the `limit` case
      asserts it.
- [ ] A full boundary trio exists for **every** numeric threshold touched, including both ends of
      any clamp (§3.6).
- [ ] `0`, negative, over-domain, absent, `null`, and non-numeric inputs each have a stated
      expectation (§3.1, §3.2).
- [ ] Outcomes are asserted **per downstream system**, not collapsed into one assertion (§2).
- [ ] Negatives assert **absence** of each downstream effect.
- [ ] One scope-negative per scoping dimension **plus** one combination negative (§4.1, §4.2).
- [ ] Cross-scope proof exists if the threshold varies by category (§3.3).
- [ ] An authorization negative exists if the resource has an owner (§4.3).
- [ ] An idempotency case exists if the rule has a side effect (§4.4).
- [ ] Test names contain the boundary value.
- [ ] Expected values are literals, not re-derived from the product's own constant (§3.5).
- [ ] No `sleep`, no wall-clock, no timezone dependence, no ordering dependence between cases; the
      suite passes twice in randomized order.
- [ ] Any case that fails because the product is genuinely wrong is **skipped/xfail with the defect
      named**, and reported — not fixed in the same PR, and never "fixed" if it is a planted
      golden-app bug (`AGENTS.md`).
- [ ] Any threshold discovered that is **not** in §8 is added to §8 with an owning WP.

---

## 8. Repo threshold audit

Every numeric threshold found in the repo, with the comparison operator actually used and whether a
boundary trio exists today. Method:

```
rg -n -g '!**/node_modules/**' -g '!**/target/**' -g '!**/.venv/**' \
   -e 'MAX_|max_|Max[A-Z]|_limit|Limit|LIMIT|_cap\b|timeout|TIMEOUT|Timeout|ttl|TTL|Ttl|per_page|page_size|pageSize|threshold|Threshold|min_|MIN_|Min[A-Z]' \
   --type-add 'src:*.{rs,go,java,kt,scala,py,rb,cs,ts,tsx,js,sh}' -tsrc \
   services/ demo-platform/ etl/ frontend/ shared/ scripts/ clients/
```
plus per-language sweeps for validation annotations (`@Size`, `validates … numericality`,
pydantic `Field(min_length=…, max_length=…)`, `Query(… ge=…, le=…)`).

**Trio column:** `no` = no `limit-1`/`limit`/`limit+1` cases exist. `partial` = the limit and one
side are covered but not both sides, or only one downstream system is asserted.

### 8.1 file-service (Rust)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-01 | `MAX_UPLOAD_BYTES` / `max_upload_bytes` | `src/config.rs:49`; enforced `src/handlers.rs:87` | 104,857,600 (100 MiB) | `>` (limit accepted) | no | WP-01 |
| T-02 | list-files `page_size` cap | `src/handlers.rs:247` | default 50, cap 100 (`.min(100)`) | clamp, inclusive | no | WP-01 |
| T-03 | list-folders `page_size` cap | `src/handlers.rs:292` | default 50, cap 100 | clamp, inclusive | no | WP-01 |
| T-04 | list-shared/trashed `page_size` cap | `src/handlers.rs:318` | default 50, cap 100 | clamp, inclusive | no | WP-01 |
| T-05 | `page` floor | `src/handlers.rs:246, 291, 317` | min 1 (`.max(1)`) | clamp, inclusive | no | WP-01 |

### 8.2 api-gateway (Go)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-06 | rate limit `rps` | `internal/config/config.go:75`; bucket `internal/middleware/ratelimit.go:60` | `RATE_LIMIT_RPS` = 100 | `tokens >= 1` | partial (`ratelimit_test.go` covers n and n+1 at rps=5; no fractional-refill boundary, no `Retry-After` assertion at the boundary) | WP-04 |
| T-07 | bucket refill rate | `internal/middleware/ratelimit.go:56-59` | `rps` tokens/sec, cap `rps` | saturating | partial | WP-04 |
| T-08 | stale-bucket eviction | `internal/middleware/ratelimit.go:97, 101` | tick 5 min, evict idle > 10 min | `>` | no | WP-04 |
| T-09 | circuit-breaker `MaxRequests` (half-open) | `internal/config/config.go:86`; `internal/proxy/circuitbreaker.go:149, 174` | `CB_MAX_REQUESTS` = 5 | `>=` | partial | WP-04 |
| T-10 | circuit-breaker `FailureRatio` | `internal/config/config.go:89`; `internal/proxy/circuitbreaker.go:198` | `CB_FAILURE_RATIO` = 0.6 | `>=` (float) | no | WP-04 |
| T-11 | circuit-breaker minimum sample size | `internal/proxy/circuitbreaker.go:194` | 5 (hard-coded) | `<` | no | WP-04 |
| T-12 | circuit-breaker `Interval` / `Timeout` | `internal/config/config.go:87-88` | 60 s / 30 s | time compare | no | WP-04 |
| T-13 | `CORS_MAX_AGE` | `internal/config/config.go:82`; `internal/middleware/cors.go:29` | 300 s | value echo | n/a (not a gate) | WP-03 |
| T-14 | server read/write/idle timeouts | `cmd/server/main.go:116-118` | 15 s / 30 s / 60 s | time compare | no | WP-03 |
| T-15 | `SHUTDOWN_TIMEOUT_SECONDS` | `internal/config/config.go:84` | 30 s | time compare | no | WP-03 |

### 8.3 auth-service (Java 17)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-16 | access-token TTL (`exp`) | `application.yml:33`; `security/JwtTokenProvider.java:27, 43` | 3,600 s | JJWT `exp` (expired when `now > exp`) | no | WP-05 |
| T-17 | refresh-token TTL | `application.yml:34`; `security/JwtTokenProvider.java:28, 56` | 2,592,000 s (30 d) | as above | no | WP-05 |
| T-18 | refresh-token expiry sweep | `repository/RefreshTokenRepository.java:25` | — | `expiresAt < :now` (strict) | no | WP-05 |
| T-19 | password length | `dto/RegisterRequest.java:13`, `dto/ChangePasswordRequest.java:12` | `@Size(min = 8, max = 128)` | inclusive both ends | no | WP-05 |
| T-20 | display-name length | `dto/RegisterRequest.java:17`, `dto/UpdateProfileRequest.java:8` | `@Size(min = 1, max = 100)` | inclusive | no | WP-05 |
| T-21 | bio length | `dto/UpdateProfileRequest.java:11` | `@Size(max = 500)` | inclusive | no | WP-05 |
| T-22 | Hikari pool size / connection timeout | `application.yml:15-17` | 10 / 2 / 30,000 ms | pool gate | no (config; assert wiring only) | WP-05 |

### 8.4 document-service (Python)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-23 | list/search `size` | `app/api/documents.py:153, 196, 214` | default 20, `ge=1, le=100` | inclusive both ends | no | WP-06 |
| T-24 | `page` floor | `app/api/documents.py:152, 195, 213` | `ge=1` | inclusive | no | WP-06 |
| T-25 | search `q` minimum length | `app/api/documents.py:151` | `min_length=1` | inclusive | no | WP-06 |
| T-26 | document/folder title length | `app/schemas/document.py:12, 22, 31, 107, 128` | `min_length=1, max_length=500` | inclusive | no | WP-06 |
| T-27 | version content minimum length | `app/schemas/document.py:89` | `min_length=1` | inclusive | no | WP-06 |
| T-28 | DB `max_overflow` | `app/config.py:15`; `app/db/session.py:14` | 20 | pool gate | no | WP-06 |
| T-29 | Redis socket timeout | `app/api/documents.py:40` | 1 s | time compare | no | WP-06 |

### 8.5 search-service (Python)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-30 | search `size` clamp | `app/api/search.py:55` | `max(1, min(100, size or 20))` | inclusive both ends | no | WP-07 |
| T-31 | advanced-search `size` clamp | `app/api/search.py:136` | `min(max(size, 1), 100)` | inclusive | no | WP-07 |
| T-32 | `page` floor | `app/api/search.py:54` | `max(1, page)` | inclusive | no | WP-07 |
| T-33 | SQS `max_messages` | `app/config.py:28`; `app/services/sqs_consumer.py:27` | 10 | SQS cap (max 10) | no | WP-07 |
| T-34 | SQS visibility timeout | `app/config.py:30`; `app/services/sqs_consumer.py:29` | 60 s | time compare | no | WP-07 |
| T-35 | consumer thread join timeout | `app/services/sqs_consumer.py:58` | 5 s | time compare | no | WP-07 |
| T-36 | `MAX_ANALYTICS_ENTRIES` ring buffer | `app/services/meilisearch_client.py:28, 39` | 10,000 | `>` then slice `[-MAX:]` | no | WP-07 |
| T-37 | indexer `FETCH_TIMEOUT` / fetch page size | `app/services/indexer.py:16, 114, 150` | 30 s / 100 | time compare / paging | no | WP-07 |
| T-38 | MeiliSearch task waits | `app/services/meilisearch_client.py:92, 273, 343, 355, 362` | 10,000 / 30,000 / 60,000 ms | time compare | no | WP-07 |
| T-39 | Redis socket timeout | `app/api/search.py:27` | 1 s | time compare | no | WP-07 |

### 8.6 notification-service (Kotlin)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-40 | notifications `page_size` | `routes/Routes.kt:62`; `repository/NotificationRepository.kt:62` | default 20, **no upper cap** | none | no — and the missing cap is itself a boundary gap to pin | WP-08 |
| T-41 | unread-count page size | `repository/NotificationRepository.kt:144` | `Int.MAX_VALUE` | none | no | WP-08 |
| T-42 | `SQS_MAX_MESSAGES` | `config/AppConfig.kt:33`; `consumer/SqsConsumer.kt:70` | 10 | SQS cap | no | WP-08 |
| T-43 | HTTP client timeout / max frame | `Application.kt:75-76` | 15,000 ms / `Long.MAX_VALUE` | time compare | no | WP-08 |

### 8.7 audit-service (C#)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-44 | query `pageSize` clamp | `src/Controllers/AuditController.cs:75` | `Math.Clamp(size ?? 20, 1, 100)` | inclusive both ends | no | WP-09 |
| T-45 | export default window | `src/Controllers/AuditController.cs:123` | `from ?? UtcNow.AddDays(-30)` | window edge | no | WP-09 |
| T-46 | `ArchiveAfterDays` | `src/Config/AwsSettings.cs:10`; `src/Services/AuditService.cs:151` | 90 d | cutoff compare | no | WP-09 |
| T-47 | archive lower bound | `src/Services/S3AuditArchiver.cs:78` | `DateTime.MinValue` .. `olderThan` | range edge | no | WP-09 |
| T-48 | suspicious-activity threshold | `src/Services/AuditService.cs:114, 117` | `Math.Max(avg × 3, 100)` | `>` (strict) | no | WP-09 |
| T-49 | report period windows | `src/Services/AuditService.cs:160-165` | 1 / 7 / 30 / 90 / 365 d, default 30 | window edge | no | WP-09 |
| T-50 | SNS consumer `MaxNumberOfMessages` | `src/Services/SnsConsumer.cs:53` | 10 | SQS cap | no | WP-09 |

### 8.8 admin-service (Ruby)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-51 | `over_quota?` | `app/models/storage_quota.rb:26` (scope: `:17`) | — | **`used_bytes >= quota_bytes`** | no (spec covers "clearly over" / "clearly under" only) | WP-10 |
| T-52 | `quota_bytes` validation | `app/models/storage_quota.rb:13` | `greater_than: 0` | strict (0 rejected) | no | WP-10 |
| T-53 | `used_bytes` validation | `app/models/storage_quota.rb:14` | `greater_than_or_equal_to: 0` | inclusive | no | WP-10 |
| T-54 | `TIER_LIMITS` | `app/models/storage_quota.rb:5-10` | 5 GB / 50 GB / 200 GB / 1 TB | per-tier constants | no — needs the §3.3 cross-scope proof | WP-10 |
| T-55 | default `quota_bytes` | `db/migrate/20240101000005_create_storage_quotas.rb:5`; `db/schema.rb:83` | 5,368,709,120 | default | no | WP-10 |
| T-56 | `per_page` clamp | `app/controllers/application_controller.rb:44` | default 20, `min(100).clamp(1, 100)` | inclusive both ends | no | WP-10 |
| T-57 | `page` floor | `app/controllers/application_controller.rb:43` | `max(1)` | inclusive | no | WP-10 |
| T-58 | feature-flag `rollout_percentage` range | `app/models/feature_flag.rb:4-8` | 0..100 inclusive | inclusive both ends | no | WP-10 |
| T-59 | feature-flag rollout bucketing | `app/models/feature_flag.rb:24` | `md5 % 100 < rollout_percentage` | **`<`** (so 0 never fires, 100 always) | no | WP-10 |
| T-60 | Rack::Attack IP throttle | `config/initializers/rack_attack.rb:1` | 300 req / 5 min | `limit` (exceeds = 429) | no | WP-10 |
| T-61 | Rack::Attack bulk throttle | `config/initializers/rack_attack.rb:5` | 10 req / 1 min | as above | no | WP-10 |
| T-62 | `CHAOS_TTL_SECONDS` | `app/controllers/api/v1/admin/chaos_controller.rb:5, 30` | 600 s | Redis `SETEX` | no | WP-10 |
| T-63 | title/name length caps | `app/models/incident.rb:17`, `announcement.rb:5`, `admin_user.rb:9` | `maximum: 255` | inclusive | no | WP-10 |
| T-64 | health/probe HTTP timeouts | `app/services/health_checker.rb:38-39, 66`; `chaos_probe_service.rb:41, 56, 79-80`; `devin_session_service.rb:96-97` | 2 / 3 / 8 / 10 / 30 s | time compare | no | WP-10 |
| T-65 | CORS `max_age` | `config/initializers/cors.rb:9` | 600 s | value echo | n/a | WP-10 |
| T-66 | Puma threads / worker timeout | `config/puma.rb:1-5` | 5 threads, 3600 s (dev) | pool gate | no (deploy config) | WP-10 |

### 8.9 report-service (Java 8) / legacy-portal (Java 11) / analytics-service (Scala)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-67 | report `max-rows` truncation | `report-service/.../config/AppConfig.java:39`; `application.properties:38`; enforced `service/ReportGenerationWorker.java:92` | 50,000 | **`>`** (exactly max not truncated) | no | WP-12 |
| T-68 | report HTTP connection/read timeout | `report-service/.../config/AppConfig.java:42, 45` | 5,000 / 30,000 ms | time compare | no | WP-12 |
| T-69 | report connection-pool caps | `report-service/.../config/AppConfig.java:52-53` | 50 total / 20 per route | pool gate | no | WP-12 |
| T-70 | feedback rating range | `legacy-portal/.../feedback/FeedbackService.java:10-11, 21` | `MIN_RATING = 1`, `MAX_RATING = 5` | `< MIN \|\| > MAX` (both inclusive) | no | WP-12 |
| T-71 | `topContent` limit | `analytics-service/.../api/AnalyticsRoutes.scala:66`; `repository/MetricsAggregator.scala:101` | default 10, **uncapped** | `.take(limit)` | no — uncapped limit is a boundary gap | WP-12 |
| T-72 | recent-activity window | `analytics-service/.../repository/MetricsAggregator.scala:43` | `.take(20)` | fixed | no | WP-12 |

### 8.10 collab-service (Node)

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-73 | `MAX_SNAPSHOTS` per document | `src/config.ts:56`; enforced `src/services/document-store.ts:121-122` | 50 | **`count > max`** then `ltrim(0, max-1)` | no | WP-11 |
| T-74 | `DOC_TTL_SECONDS` | `src/config.ts:54`; `src/services/document-store.ts:44, 60, 77` | 86,400 s | Redis TTL | no | WP-11 |
| T-75 | `SNAPSHOT_TTL_SECONDS` | `src/config.ts:55`; `src/services/document-store.ts:45, 125` | 604,800 s | Redis TTL | no | WP-11 |
| T-76 | `getSnapshots` default limit | `src/services/document-store.ts:137` | 20 (`lrange 0, limit-1`) | off-by-one prone | no | WP-11 |
| T-77 | persist / snapshot intervals | `src/config.ts:52-53` | 30,000 / 300,000 ms | timer | no | WP-11 |
| T-78 | presence cleanup interval / idle eviction | `src/handlers/presence.ts:45-46` | 60,000 / 300,000 ms | `maxIdleMs` compare | no | WP-11 |
| T-79 | socket.io `pingTimeout` | `src/index.ts:91` | 20,000 ms | time compare | no | WP-11 |
| T-80 | forced-shutdown timer | `src/index.ts:211` | timer | time compare | no | WP-11 |
| T-81 | Jest `coverageThreshold` | `jest.config.js:14-20` | **all zero** | gate | n/a — the gate exists but is set to 0 | WP-00 |

### 8.11 frontends

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-82 | `MAX_PREVIEW_SIZE` | `frontend/client-app/src/components/files/file-preview.tsx:4, 30` | 500,000 B; `Range: bytes=0-(MAX-1)` | off-by-one prone | no | WP-14 |
| T-83 | API client page sizes | `frontend/client-app/src/lib/api.ts:162, 282, 295, 331, 408` | 50 / 50 / 50 / 50 / 20 | default | no | WP-14 |
| T-84 | storage-sum `PAGE_LIMIT` | `frontend/client-app/src/lib/api.ts:474` | 100 (must match T-02) | must equal server cap | no — cross-service coupling | WP-14 |
| T-85 | `hasMore` computation | `frontend/client-app/src/lib/api.ts:172, 292, 308, 395` | `page × pageSize < total` | **`<`** | no | WP-14 |
| T-86 | trash page size | `frontend/client-app/src/pages/trash.tsx:72` | 50 | default | no | WP-14 |
| T-87 | Playwright global / expect timeouts | `frontend/client-app/playwright.config.ts:12-13, 37` | 30,000 / 10,000 / 120,000 ms | time compare | n/a (harness) | WP-15 |
| T-88 | admin paginator options | `admin-dashboard/.../users.component.ts:133`, `audit.component.ts:114`, `quotas.component.ts:103` | [5,10,25] / [10,25,50] / [5,10,25] | selector | no | WP-16 |
| T-89 | admin audit fetch `per_page` | `admin-dashboard/.../core/services/admin-api.service.ts:63` | 20 | default | no | WP-16 |

### 8.12 demo-platform (control plane, reaper) and ETL

| # | Threshold | Where | Value | Operator | Trio today | Owning WP |
|---|---|---|---|---|---|---|
| T-90 | `IDLE_AFTER_SECONDS` | `demo-platform/reaper/idle-suspend.sh:47, 250` | 3,600 s | **`-lt` → suspends at exactly the threshold** | no (`test-idle-suspend.sh` uses 7200 s and an under-threshold case; the exact-boundary second is untested) | WP-21 |
| T-91 | reaper `grace_seconds` | `demo-platform/reaper/reaper.sh:236, 401` | default 0 | `exp + grace < now` (strict) | no | WP-21 |
| T-92 | tenant default TTL | `scripts/deploy-tenant.sh:38`; `demo-platform/runner/entrypoint.sh:49`; `demo-platform/scripts/tenant.sh:43` | 8 h | TTL | no | WP-21 |
| T-93 | CD tenant TTL | `demo-platform/scripts/tenant.sh:47` | 72 h | TTL | no | WP-21 |
| T-94 | `NEVER_TTL_SECONDS` sentinel | `demo-platform/dashboard/lib/util.ts:21`; `app/api/tenants/[id]/extend/route.ts:21`; `.../persist/route.ts:43` | 10 × 365 × 86,400 | **`>=` rejects** | no | WP-21 |
| T-95 | `UNPERSIST_TTL` | `demo-platform/dashboard/app/api/tenants/[id]/persist/route.ts:14` | 24 h | TTL | no | WP-21 |
| T-96 | login rate limit (per IP / global) | `demo-platform/dashboard/lib/ratelimit.ts:16-17, 44, 58` | 5 / 20 attempts | **`>=`** | no | WP-21 |
| T-97 | dashboard session TTL | `demo-platform/dashboard/lib/env.ts:77`; `lib/session.ts:37, 86` | `SESSION_TTL_SECONDS` | `exp` compare | no | WP-21 |
| T-98 | k8s cache TTL | `demo-platform/dashboard/lib/k8s.ts:85, 93` | 5,000 ms | `<` | no | WP-21 |
| T-99 | checkout lock TTL | `demo-platform/dashboard/lib/control.ts:140` | 15 × 60 s | DynamoDB TTL | no | WP-21 |
| T-100 | job `ttlSecondsAfterFinished` / `backoffLimit` | `demo-platform/dashboard/lib/jobs.ts:93-94` | 3,600 s / 1 | k8s | no | WP-21 |
| T-101 | ETL `lookback_days` | `etl/scripts/user_activity_daily.py:42` | 30 d | window edge | no | WP-20 |
| T-102 | ETL audit `retention_days` | `etl/scripts/audit_archive_weekly.py:47, 53` | 90 d | cutoff compare | no | WP-20 |
| T-103 | ETL DynamoDB batch size | `etl/scripts/audit_archive_weekly.py:49, 146` | 25 | **`>=`** flush | no | WP-20 |
| T-104 | ETL reindex batch / page size | `etl/scripts/search_reindex_weekly.py:35-36, 207, 271` | 500 / 100 | `<` last-page test | no | WP-20 |
| T-105 | ETL analytics drain caps | `etl/scripts/analytics_daily.py:62-63, 68, 82` | 10,000 msgs / batch 10 / `consecutive_errors >= 3` | `<` and `>=` | no | WP-20 |
| T-106 | node group max size | `platform/terraform/environments/dev.tfvars:45`; `modules/eks/main.tf:139` | 3 | scaling cap | no | WP-23 |
| T-107 | OTel `send_batch_max_size` | `observability/otel/otel-collector-config.yml:13` | 2,048 | batching | no | WP-23 |

### 8.13 Thresholds with no owning WP today

These fall outside every ownership glob in the SOW §5 table. They are listed here so the gap is
explicit; each needs an owner assigned before it can be closed.

| # | Threshold | Where | Value | Operator | Suggested owner |
|---|---|---|---|---|---|
| T-108 | `CHAOS_TTL` (bug injection) | `scripts/inject-bug.sh:45, 69` | 3,600 s | Redis `SETEX` | new WP (repo `scripts/`) |
| T-109 | `DRAIN_TIMEOUT` (cluster teardown) | `scripts/teardown-cluster.sh:26, 122, 135` | 300 s | deadline compare | new WP (repo `scripts/`) |
| T-110 | tenant DB-init job waits | `scripts/deploy-tenant.sh:298`; `scripts/lib/tenant-common.sh:242` | 90-120 s | `kubectl wait` | new WP (repo `scripts/`) |
| T-111 | namespace TTL reaper job history / `ttlSecondsAfterFinished` | `scripts/tenant-platform-baseline.sh:81-85` | 3 / 3 / 600 s | k8s | new WP (repo `scripts/`) |
| T-112 | seeded `max_upload_size_mb` / `session_timeout_minutes` / `default_storage_quota_gb` | `services/admin-service/db/seeds.rb:4, 16, 22` | seed values | seed data — must agree with T-01/T-55 | WP-10 (drift assertion) |
| T-113 | seeded `max_upload_size_bytes` (10 GB) | `testdata/generated/seed/seed_10_incidents_configs.py:362` | 10,737,418,240 | fixture value **inconsistent with T-01 (100 MiB)** | see finding in the PR |

**Cross-cutting consistency assertions worth their own cases** (no single WP sees both sides):
T-02 vs. T-84 (client page cap must equal server cap); T-01 vs. T-112/T-113 (seeded config vs. the
enforced constant); T-40 (uncapped) vs. every other service's cap of 100.

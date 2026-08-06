# OtterWorks — Test Coverage Expansion: Scope of Work

**Repo:** `Cognition-Partner-Workshops/otterworks` @ `b20c699` · **Date:** 2026-08-06 · **Status:** plan only, no code written.

This document is the contract for a fan-out of parallel workers. Each work package (WP) owns a
**disjoint set of files**, so every WP can run simultaneously on its own branch and PR.

---

## 1. Executive summary

| | |
|---|---|
| Build units in repo | 17 (12 backend services, 2 frontends, demo-platform, ETL, Windows desktop client) |
| Units with **zero** automated tests | **4** — `etl/`, `clients/windows-desktop/`, `demo-platform/dashboard/`, `services/file-service/src/storage.rs`+`middleware.rs`+`config.rs` |
| Total unit/integration cases | ≈ 525 |
| Coverage thresholds enforced anywhere | **None** — coverage is produced by 4 suites, gated by 0 |
| Suites that exist but **never execute in CI** | 3 — Playwright e2e (70), Cucumber BDD (36), black-box API flows (24, collect-only) |
| Suites that **cannot fail** CI | `admin-dashboard` (`|| true`), `client-app` (`--passWithNoTests`) |
| Dominant gap shape | positive-path bias; boundary (`limit±1`), authz-negative, and concurrency/idempotency cases are largely absent |

The single highest-leverage fix is not more tests — it is **WP-00**: no coverage number is measured
or enforced today, so no worker's output can be scored. Land it first.

---

## 2. Current coverage map (evidence-based)

Cases counted by test-annotation grep; LOC excludes test dirs.

| Build unit | Lang / framework | Cases | Src LOC | Cases/KLOC | In CI | Can fail CI | Coverage measured |
|---|---|---:|---:|---:|:--:|:--:|:--:|
| `services/analytics-service` | Scala / ScalaTest | ~56 | 2,458 | 23 | yes | yes | no |
| `services/admin-service` | Ruby / RSpec | 87 | 2,438 | 36 | yes | yes | no |
| `services/report-service` *(legacy Java 8)* | JUnit 4 | 45 | 2,295 | 20 | yes | yes | no |
| `services/document-service` | Python / pytest | 44 | 1,563 | 28 | yes | yes | `--cov` (no gate) |
| `services/search-service` | Python / pytest | 41 | 1,482 | 28 | yes | yes | `--cov` (no gate) |
| `services/api-gateway` | Go / testing | 33 | 2,111 | 16 | yes | yes | profile only |
| `services/auth-service` | Java 17 / JUnit 5 | 29 | 1,217 | 24 | yes | yes | jacoco (local only) |
| `services/notification-service` | Kotlin / JUnit 5 | 28 | 1,280 | 22 | yes | yes | no |
| `services/audit-service` | C# / xUnit | 24 | 1,437 | 17 | yes | yes | no |
| `services/collab-service` | Node / Jest | 45 | 1,659 | 27 | yes | yes | no |
| `services/legacy-portal` | Java 11 / JUnit 5 | 13 | 814 | 16 | yes | yes | no |
| **`services/file-service`** | **Rust / inline `#[test]`** | **9** | **2,617** | **3.4** | yes | yes | no |
| `frontend/admin-dashboard` | Angular / Karma | 64 | 4,506 | 14 | yes | **no** (`|| true`) | no |
| **`frontend/client-app`** | **Vitest** | **4** | **9,308** | **0.4** | yes | **no** (`--passWithNoTests`) | no |
| `frontend/client-app/e2e` | Playwright | 70 | — | — | **no** | no | — |
| `frontend/client-app/bdd` | Cucumber | 36 scenarios | — | — | **no** | no | — |
| `tests/api` (black-box flows) | pytest vs. live gateway | 24 | — | — | **collect-only** | no | — |
| `tests/contract` | pytest | 18 | — | — | not wired | no | — |
| `demo-platform/reaper` | bash | 3 scripts | 2,900 | — | yes | yes | no |
| **`etl/`** | — | **0** | 1,467 | **0** | no | no | no |
| **`clients/windows-desktop`** | — | **0** | 1,244 | **0** | no | no | no |
| **`demo-platform/dashboard`** | — | **0** (typecheck only) | — | 0 | typecheck | yes | no |

### Harness-level defects found while inventorying

1. **`make test` is broken** — targets `frontend/web-app`, which does not exist (the app is
   `frontend/client-app`). It also omits `report-service`, `legacy-portal`, `tests/api`,
   `tests/contract`, e2e and BDD. (`Makefile:114-126`)
2. **`api-flow-tests` CI job only collects** — `pytest tests/api --collect-only -q`
   (`.github/workflows/ci.yml:428`). 24 integration flows are syntax-checked, never executed. There
   is no docker-compose stand-up step in CI.
3. **`tests/contract` (18 cases) is in no Makefile target and no CI job.** Dead code today.
4. **Two suites are unfailable**: `admin-dashboard` lint+test are `|| true` (`ci.yml:315-316`);
   `client-app` `npm test` is `vitest run --passWithNoTests` against 4 tests over 9.3 KLOC.
5. **`make test-coverage` swallows every failure** (`|| true` on all 7 lines) and prints no
   aggregate — coverage cannot be trended or gated.
6. **SonarCloud misclassifies tests** — `sonar-project.properties` sets both `sonar.sources` and
   `sonar.tests` to `services,frontend`, and `sonar.java.binaries=.`, so quality-gate figures for
   this repo should not be trusted as a coverage baseline.

---

## 3. Gap analysis — what is missing, by unit

Ordered by risk = blast radius x regression likelihood x cheapness to cover.

### Tier 1 — high risk, cheap to close

**`file-service` (Rust, 2,617 LOC, 9 tests, 3.4/KLOC — the worst ratio in the backend).**
Tested: `metadata` parsing (7), `events` serialization (2), `health`/`metrics` handlers (2).
Untested: **all 20 business handlers** in `handlers.rs` (upload, download, move, rename, trash,
restore, share, remove_share, folder CRUD, versions, activity), plus `storage.rs` (S3),
`middleware.rs` (auth header extraction), `config.rs`, `errors.rs`, `models.rs`.
Missing cases: upload at `MAX_UPLOAD_BYTES` (100 MB default, `config.rs:47`) −1/=/+1; zero-byte
file; missing multipart part; filename with unicode/path-traversal (`../`); download of a trashed
file; restore of a non-trashed file; double-trash idempotency; share with self; share with an
unknown permission string; remove a share that does not exist; folder cycle (move a folder into
its own descendant); delete a non-empty folder; version list on a file with 0 and with N versions;
cross-user access to every route (currently unproven).

**`api-gateway` (Go).** `circuitbreaker.go` is well tested (160 lines of tests); `router.go`
(120 LOC, the actual routing table) has **no test at all**, nor do `logging.go`, `metrics.go`,
`config.go`. Missing: unknown prefix → 404 vs. 502; the four documented **route gaps** (`/templates`,
`/folders`, `/reports`, `/preferences` are served by services but absent from `ServiceRoutes` —
`docs/api-route-matrix.md`) should each be pinned by a test asserting today's behavior so the fix is
detectable; rate limiter at exactly `rps`, `rps+1`, and refill after the window; JWT expired /
`alg:none` / wrong issuer / missing `sub`; `X-User-ID` spoofing by the client (header injection —
gateway sets it from claims, some services trust it blindly).

**`auth-service` (Java).** 7 service cases + JWT provider + one controller integration test. Missing:
refresh-token replay/rotation, logout invalidating an in-flight token, password-policy boundaries
(min/max length, unicode), email casing + duplicate-registration race, account lockout/throttling,
token `exp`/`nbf` clock-skew edges, and admin-only route authorization.

**`client-app` (React, 9.3 KLOC, 4 unit tests).** Only `corporate.test.ts`. Nothing covers the
document editor, file upload UI, share dialog, trash/restore, search, or the API client's error
handling. Playwright covers 70 flows but never runs in CI, so it protects nothing today.

### Tier 2 — real gaps, more setup cost

**`tests/api` (black-box).** Good structure and honest markers (`gap_revealer`, `side_effect`,
`degradation`), but never executed. Also missing suites the route matrix itself lists as "planned":
files/folders lifecycle depth, reports, audit archive, preferences.

**`document-service` / `search-service` (Python).** Solid API-level tests. Untested modules:
`services/event_publisher.py` (SNS fanout), `db/session.py`, `config.py` in document-service;
`services/sqs_consumer.py`, `middleware/auth.py`, `services/meilisearch_client.py` in search-service.
Missing: pagination bounds (`page=0`, `page=-1`, beyond last page), max-length titles, concurrent
version writes, restore of a deleted version, comment on a deleted doc, search query of length 0 /
very long / injection-ish syntax, reindex idempotency, MeiliSearch unavailable.

**`audit-service` (C#).** 24 cases over repository/archiver. Untested: `SnsConsumer`,
`AuditController`, both middlewares, and compliance/export report generation. Audit is
compliance-relevant — immutability (no update/delete path), duplicate event suppression, and
export-window boundaries deserve explicit negative tests.

**`notification-service` (Kotlin).** `SqsConsumer` has tests; missing: malformed message → DLQ,
duplicate delivery, unknown template id, preferences opt-out honored, and the **`400` on
`/api/v1/notifications` + `/unread-count` already documented in `docs/exploratory-qa-report.md`** —
that live bug has no regression test.

**`admin-service` (Ruby).** Best-covered service (87). Gaps are boundary-shaped: `StorageQuota`
at exactly `quota_bytes` (`over_quota?` uses `>=`), quota of 0/negative (validation says
`greater_than: 0` — assert the rejection), bulk operations partial failure/rollback, feature-flag
precedence, and admin authorization negatives.

**`analytics-service` (Scala).** 56 cases, strongest suite. Gaps: rollup with empty input window,
DST/timezone bucket edges, duplicate event idempotency, and very large aggregation windows.

### Tier 3 — zero-coverage units

- **`etl/scripts/*.py`** (5 cron jobs, 1,467 LOC): no tests, no CI job at all. Highest-value cheap
  win after Tier 1 — these are pure-ish functions over a DB.
- **`clients/windows-desktop`** (WPF/C#, 1,244 LOC): no test project, not in CI.
- **`demo-platform/dashboard`** (Next.js control plane that provisions tenants): typecheck only.
  `demo-platform/lib/control-common.sh` and `runner/entrypoint.sh` are untested; the reaper's
  `sweep_orphan_dbs` hyphen bug (known: it GC'd a live tenant) is exactly the class of defect a
  unit test on id-derivation would have caught.
- **`shared/openapi`, `shared/proto`, `shared/events`**: no schema-conformance or
  backward-compatibility check; `tests/contract` exists but is unwired.
- **`infrastructure/helm`, `infrastructure/terraform`**: `fmt`/`validate` only — no `helm template`
  assertions, no policy tests (e.g. "no Service of type LoadBalancer except ingress-nginx", a rule
  `AGENTS.md` states and `deploy-dev.sh` enforces at runtime but nothing tests).

### Cross-cutting gaps (no unit owns these today)

- **Authorization matrix**: no systematic "user A cannot touch user B's resource" sweep across
  files, documents, comments, shares, notifications, audit.
- **Idempotency / concurrency**: no double-submit, retry, or concurrent-write tests anywhere.
- **Contract drift**: services are versioned independently with no consumer-driven contract gate.
- **Performance / load**: none (no k6/Gatling/Locust).
- **DAST**: none (`docs/SDLC-COVERAGE.md` §7 confirms SAST-only).
- **Accessibility**: none in the Playwright suite.

---

## 4. How the BRD relates

**The BRD (`Credit_Score_Decline_Rules_BRD.docx`) describes an insurance domain — Agency Portal →
Guidewire PolicyCenter, MA / Home / HO4 / HO6 auto-decline on credit score. None of that exists in
OtterWorks.** A full-text search for `guidewire|policycenter|declination|credit score|agency portal|
underwriting` returns **zero matches** in the repo. So it cannot be "covered" by adding tests to
OtterWorks as-is.

It is still directly useful in two ways — pick one, or do both:

### 4a. As the QE pattern this whole program should adopt (recommended, zero new product code)

The BRD is a textbook **decision table with numeric thresholds and a four-quadrant outcome matrix**.
That is precisely the shape OtterWorks' own rules have and precisely where its tests are thinnest.
Adopt the BRD's structure as the house style for threshold rules, and apply it to the repo's real
decision tables:

| BRD concept | OtterWorks analogue with the same shape | Today |
|---|---|---|
| `Credit Score < 590` → decline | `MAX_UPLOAD_BYTES` (100 MB) → reject upload (`file-service/src/config.rs:47`) | untested |
| Rule differs by Policy Type (HO4/HO6) | Share permission tier changes allowed actions (`metadata.rs` `share_permission_from_str`) | 1 parse test, no matrix |
| Threshold breach → decline + notice | `used_bytes >= quota_bytes` → over-quota (`admin-service/app/models/storage_quota.rb:26`) | boundary untested |
| Rate/eligibility gate | Gateway rate limiter `rps` token bucket | refill/boundary untested |
| Decline notice **sent** vs. **generated** (two systems) | Event fanout: action in service A → notification/audit record in service B | 3 `side_effect` tests, never run in CI |

Concretely: **WP-13** below produces `docs/bdd/decision-table-testing-standard.md` — a BRD-style
template (rule id, inputs, condition, expected outcome per system, boundary trio) that every other
WP uses when it touches a threshold. That is how the BRD gets "addressed" without inventing an
insurance feature.

### 4b. If the intent is to actually build/validate the rule engine

Then it is a **new feature**, not a coverage gap, and it needs its own product scope. What the
coverage program can pre-build is the **test matrix**, which is fully derivable from the BRD today
and is reusable against whatever implements it (a real Agency Portal, a mock, or an OtterWorks
demo variant). Full matrix — **24 cases**:

*Boundary trio per rule (the BRD's core risk: `<` vs `<=`):*

| # | Rule | Policy type | Credit score | AP outcome | Notice sent to GWPC | Notice generated in GWPC |
|---|---|---|---|---|---|---|
| B1 | R1 | HO6 | 589 | Declined | Yes | Yes |
| B2 | R1 | HO6 | **590** | **Not declined** | No | No |
| B3 | R1 | HO6 | 591 | Not declined | No | No |
| B4 | R2 | HO4 | 579 | Declined | Yes | Yes |
| B5 | R2 | HO4 | **580** | **Not declined** | No | No |
| B6 | R2 | HO4 | 581 | Not declined | No | No |
| B7 | R1 | HO6 | 579 | Declined | Yes | Yes | *(HO6 declines below HO4's threshold too — proves thresholds are not swapped)* |
| B8 | R2 | HO4 | 585 | **Not declined** | No | No | *(would decline under HO6's 590 — proves per-policy-type routing)* |

*Scope negatives (rule must NOT fire outside the BRD's stated scope) — each expects "not declined,
no notice":*

| # | Varied dimension | Value | Score |
|---|---|---|---|
| N1 | Source of Quote | Direct / Call Center (not Agency Portal) | 500 |
| N2 | State | CT, NH, RI (any non-MA) | 500 |
| N3 | LOB | Auto (not Home) | 500 |
| N4 | Policy Type | HO3, HO5 (not HO4/HO6) | 500 |
| N5 | Transaction | Endorsement, Renewal, Rewrite (not New Business) | 500 |
| N6 | Combination | Agency Portal + MA + Home + HO6 + **Renewal** | 500 |

*Guidewire-side quadrants (§5.3 / §5.4) — the asymmetry is the thing to pin:*

| # | Entry point | Criteria met | Expected |
|---|---|---|---|
| G1 | GW direct | yes | Quote **blocked**, **no** declination notice generated |
| G2 | GW direct | no | Policy issued successfully, no notice |
| G3 | AP → GWPC | yes | Declined in AP, notice **sent and generated** in GWPC |
| G4 | AP → GWPC | no | Issued, no notice anywhere |

*Edge / data-integrity cases the BRD does not specify — these are the questions to take back to the
business analyst, and each should exist as a test once answered:*

| # | Case | Open question |
|---|---|---|
| E1 | Credit score **missing / no-hit / thin file** | Decline, refer, or proceed? |
| E2 | Credit score `0`, negative, or > 850 | Reject as invalid input, or evaluate? |
| E3 | Score returned as string / null / non-numeric | Hard error vs. treat as no-hit |
| E4 | Credit bureau service timeout or 5xx | Fail-open (issue) or fail-closed (decline)? Must be explicit |
| E5 | Two applicants (co-applicant) on one HO6 quote | Which score governs — primary, lowest, or average? |
| E6 | Score changes between quote and bind | Re-evaluate at bind, or honor the quote-time decision? |
| E7 | Duplicate submit of the same quote | Exactly one declination notice (idempotency) |
| E8 | MA-frozen / consumer-blocked credit file | Legal path differs from a low score |
| E9 | Test-data trigger (§4: first name + last name + DOB + address) mismatch | A stale test identity silently produces the wrong band — the test data itself needs a fixture-integrity assertion |
| E10 | Notice generated in GWPC but AP transaction rolls back | No orphaned declination notice |

**Note on §4 of the BRD:** the credit band is triggered by *test-data identity* (name/DOB/address),
not by an injectable score. That makes every one of the above cases dependent on a maintained
identity→band fixture set. Whoever owns this should treat "the fixture bank is correct and
current" as a **tested precondition** (a smoke test that each designated identity still returns its
intended band), otherwise the whole suite silently rots. This repo already has the machinery for
that pattern in `testdata/harness/validate.py` + `make testdata-validate NS=<ns>`.

---

## 5. Work packages

Effort: **S** ≈ half a session, **M** ≈ one session, **L** ≈ one full session with setup risk.
Ownership globs are disjoint — that is what makes the fan-out safe.

| WP | Title | Owns (files/globs) | Effort | Depends on |
|---|---|---|---|---|
| **WP-00** | Coverage baseline + gates | `Makefile` (test targets), `.github/workflows/ci.yml`, `sonar-project.properties`, `codecov.yml` (new) | M | — |
| WP-01 | file-service handlers: upload/download/versions | `services/file-service/src/handlers.rs` (tests), `src/storage.rs`, new `services/file-service/tests/` | L | WP-00 |
| WP-02 | file-service: folders, trash/restore, share matrix | `services/file-service/src/metadata.rs` (tests), `src/models.rs`, `src/middleware.rs`, `src/config.rs`, `src/errors.rs` | L | WP-00 |
| WP-03 | api-gateway router + JWT/header-spoofing negatives | `services/api-gateway/internal/proxy/router_test.go` (new), `internal/config/*_test.go`, `internal/middleware/jwt_test.go`, `logging_test.go`, `metrics_test.go` | M | WP-00 |
| WP-04 | api-gateway rate-limit + circuit-breaker boundaries | `services/api-gateway/internal/middleware/ratelimit_test.go`, `internal/proxy/circuitbreaker_test.go` | S | WP-00 |
| WP-05 | auth-service: token lifecycle + password policy | `services/auth-service/src/test/**` | M | WP-00 |
| WP-06 | document-service: pagination, versions, event publisher | `services/document-service/tests/**` | M | WP-00 |
| WP-07 | search-service: query edges, SQS consumer, auth middleware | `services/search-service/tests/**` | M | WP-00 |
| WP-08 | notification-service: DLQ, dedupe, preferences + regression test for the live `400` bug | `services/notification-service/src/test/**` | M | WP-00 |
| WP-09 | audit-service: controller, SNS consumer, immutability, export windows | `services/audit-service/tests/**` | M | WP-00 |
| WP-10 | admin-service: quota/flag boundaries, bulk partial failure, authz negatives | `services/admin-service/spec/**` | M | WP-00 |
| WP-11 | collab-service: awareness/CRDT concurrency + disconnect edges | `services/collab-service/src/__tests__/**` | M | WP-00 |
| WP-12 | analytics + report + legacy-portal boundary pass | `services/analytics-service/src/test/**`, `services/report-service/src/test/**`, `services/legacy-portal/src/test/**` | M | WP-00 |
| **WP-13** | **BRD decision-table testing standard + threshold audit** | `docs/bdd/decision-table-testing-standard.md` (new), `docs/bdd/brd-credit-decline-matrix.md` (new) | S | — |
| WP-14 | client-app unit tests: API client, hooks, editor state | `frontend/client-app/src/**/*.test.ts(x)` | L | WP-00 |
| WP-15 | Wire Playwright e2e + BDD into CI, de-flake, delete stale `test-results/` | `frontend/client-app/e2e/**`, `frontend/client-app/bdd/**`, `playwright.config.ts`, e2e CI job | L | WP-00 |
| WP-16 | admin-dashboard: remove `|| true`, fix/expand Angular specs | `frontend/admin-dashboard/src/**/*.spec.ts` + its CI job | M | WP-00 |
| WP-17 | Execute `tests/api` in CI against a composed stack | `tests/api/**`, docker-compose CI job | L | WP-00 |
| WP-18 | Cross-service authorization matrix suite | `tests/authz/**` (new) | L | WP-17 |
| WP-19 | Wire + extend `tests/contract`; schema back-compat gate on `shared/` | `tests/contract/**`, `shared/openapi/**` (read-only), contract CI job | M | WP-00 |
| WP-20 | ETL job tests (5 cron scripts) | `etl/scripts/**`, `etl/tests/**` (new), ETL CI job | M | WP-00 |
| WP-21 | demo-platform: dashboard API tests + `control-common.sh` / tenant-id derivation | `demo-platform/dashboard/**`, `demo-platform/lib/**`, `demo-platform/reaper/test-*.sh` | M | WP-00 |
| WP-22 | Windows desktop client test project | `clients/windows-desktop/**` | M | WP-00 |
| WP-23 | IaC policy tests (`helm template` assertions, no stray LoadBalancer) | `infrastructure/**/tests/**` (new), IaC CI job | M | WP-00 |
| WP-24 | Load/performance smoke (k6) on the golden path | `tests/perf/**` (new) | M | WP-17 |

### Detailed specs for the first wave

**WP-00 — Coverage baseline + gates** *(land before everything else)*
- Fix `make test`: `frontend/web-app` → `frontend/client-app`; add `report-service`,
  `legacy-portal`, `tests/contract`.
- Make `make test-coverage` fail on error (drop the seven `|| true`), emit machine-readable reports
  (`coverage.xml` / `lcov.info` / `cobertura`) per unit, and print an aggregate table.
- Add per-unit coverage upload + a **ratchet** (coverage may not decrease; no absolute target yet).
- Remove `|| true` from the `admin-dashboard` CI job and `--passWithNoTests` from `client-app`
  (may be done here or deferred to WP-16/WP-14 — declare which, so the two do not collide).
- Fix `sonar-project.properties`: `sonar.sources` and `sonar.tests` must not both be
  `services,frontend`.
- **Acceptance:** `make test` and `make test-coverage` both exit non-zero on a seeded failure; CI
  publishes a per-unit coverage table on every PR; the baseline numbers are recorded in this doc.

**WP-01 — file-service handlers (highest-risk single package)**
- Cases (minimum): upload at `MAX_UPLOAD_BYTES`−1 / = / +1; zero-byte; missing multipart field;
  unicode + `../` filename; content-type mismatch; download of trashed/absent/other-user's file;
  version list at 0 and N; S3 error propagation (`storage.rs` with a stubbed client);
  double-upload idempotency; concurrent upload of the same name.
- **Acceptance:** `cargo test` green twice in a row; `cargo llvm-cov` shows `handlers.rs` +
  `storage.rs` moving from ~0% to ≥60%; no change to any `src/**` non-test line.

**WP-03 — api-gateway router**
- Pin current behavior for the four known route gaps (`/templates`, `/folders`, `/reports`,
  `/preferences`) with tests named `..._is_currently_unrouted_see_route_matrix`, so closing the gap
  turns them red deliberately.
- JWT negatives: expired, `nbf` in future, `alg: none`, wrong signature, missing `sub`, malformed
  bearer prefix. Header spoofing: client-supplied `X-User-ID` must not survive.
- **Acceptance:** `go test -race ./...` green; `internal/proxy` and `internal/config` ≥70% statement
  coverage.

**WP-13 — BRD standard** *(no product code, parallel-safe with everything)*
- Write the decision-table template (rule id, dimensions, condition, expected outcome **per
  downstream system**, mandatory boundary trio, mandatory scope-negatives).
- Transcribe §4b of this document into `docs/bdd/brd-credit-decline-matrix.md` as the worked
  example, including the E1–E10 open questions as a explicit "questions for the BA" section.
- Audit the repo for every numeric threshold (`MAX_UPLOAD_BYTES`, `quota_bytes`, rate-limit `rps`,
  pagination caps, token TTLs, TTL/reaper windows) and list which WP owns each one's boundary trio.
- **Acceptance:** every threshold in the repo appears in the table with an owning WP.

### Suggested worker prompt (per WP)

> Execute **`<WP-ID>`** from `docs/TEST-COVERAGE-EXPANSION-SOW.md` in `Cognition-Partner-Workshops/otterworks`.
> Touch only the files that package owns — if you need anything outside them, stop and report.
> Add positive, negative, and edge/boundary cases per the package spec; for every numeric
> threshold, test `limit-1`, `limit`, and `limit+1`. Do not modify production code, do not edit or
> delete existing tests, and do not "fix" a planted bug (see `AGENTS.md` — the golden app's planted
> bugs are a feature). If a new test fails because the product is genuinely wrong, keep it as
> skipped/expected-fail with a comment naming the defect and report it as a finding. Prove
> determinism by running the suite twice in random order. Open a PR listing cases added by
> category, the coverage delta, and any defects found.

---

## 6. Execution plan

**Wave 0 (serial, 1 worker):** WP-00. Everything else is scored against its baseline.
Run WP-13 in parallel here — it is docs-only and blocks nothing.

**Wave 1 (12 workers, fully parallel):** WP-01 … WP-12. Backend unit/integration depth. No two
packages share a file. Expect ~350–450 new cases.

**Wave 2 (6 workers, parallel):** WP-14, WP-15, WP-16, WP-17, WP-19, WP-20. Frontend + the three
suites that exist but never run. WP-17 is the long pole (needs a composed stack in CI).

**Wave 3 (4 workers):** WP-18, WP-21, WP-22, WP-23, then WP-24. Cross-cutting and zero-coverage
units.

Merge order = wave order. Re-run the §2 inventory after each wave and record the delta in this
document.

## 7. Guardrails (non-negotiable for every worker)

1. **`AGENTS.md` golden-app policy wins.** `main` is the demo baseline and contains *deliberately
   planted* bugs (e.g. `services/admin-service/config/environments/production.rb`). A test that
   catches a planted bug must **document** it (skipped/expected-fail), never fix it. If unsure
   whether a defect is planted or genuine, ask before changing anything.
2. **No production-code changes in a coverage PR.** Defect fixes are separate PRs with their own
   review.
3. **No existing test is edited or deleted** to make a new one pass.
4. **Determinism**: no `sleep`-based waits, no wall-clock dependence, no inter-test ordering, no
   shared mutable fixtures. Suites must pass twice, in random order.
5. **One PR per work package**, branch `devin/<ts>-wp-NN-<slug>`. Per the org's duplicate-PR
   knowledge: if a branch or PR for the same WP already exists, open a **new** PR from a fresh
   branch and cross-reference it — never overwrite or close the earlier run.
6. **Don't chase the percentage.** A PR that raises coverage without adding a negative or boundary
   case should be rejected in review.

## 8. Open questions for you

1. **BRD intent** — pattern-only (§4a, ~1 doc package) or is an actual credit-decline rules feature
   coming into scope (§4b, needs a product spec first)?
2. **Coverage target** — ratchet-only (no regressions) to start, or a hard floor (e.g. 70% per unit)
   with a deadline?
3. **Wave 2 CI cost** — running `tests/api` and Playwright in CI means standing up docker-compose
   per PR (~10-15 min). Acceptable on every PR, or nightly + on-merge only?
4. **Zero-coverage units** — is `clients/windows-desktop` (WP-22) still live enough to be worth
   tests, or is it a display artifact?

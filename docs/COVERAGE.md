# Test coverage harness

How coverage is measured, reported and gated in this monorepo. Implements WP-00
of [`TEST-COVERAGE-EXPANSION-SOW.md`](./TEST-COVERAGE-EXPANSION-SOW.md).

## Why this exists

Before WP-00 there was no number to act on:

- `make test` pointed at `frontend/web-app`, a directory that no longer exists,
  and omitted `report-service`, `legacy-portal` and `tests/contract` entirely.
- `make test-coverage` ended all seven of its lines with `|| true`, so it always
  exited 0 — a suite could be deleted outright and the target stayed green.
- Only four of fourteen units emitted coverage at all, in four incompatible
  formats, with no aggregate and nothing comparing one run to the next.
- `sonar.sources` and `sonar.tests` were both `services,frontend`, so Sonar could
  not tell test code from production code.

## The moving parts

| File | Role |
|---|---|
| `scripts/coverage/units.sh` | Single source of truth: for each build unit, its directory, how it is tested, the report format, and where the report lands. |
| `scripts/coverage/run-coverage.sh` | Runs the units, collects reports into `coverage/<unit>/`, prints the table, exits non-zero if any unit failed. |
| `scripts/coverage/aggregate.py` | Normalises Go coverprofile / LCOV / Cobertura / JaCoCo / SimpleCov into one comparable table. |
| `scripts/coverage/ratchet.py` | Fails if a unit dropped below `coverage-baseline.json`. |
| `coverage-baseline.json` | The recorded floor per unit. |
| `.github/workflows/ci.yml` → `coverage-report` | Aggregates the per-job artifacts, publishes the table on the PR, runs the ratchet. |

## Local use

```bash
make test                  # every suite; fails on the first failing unit
make test-coverage         # every suite with coverage + aggregate table
make coverage-aggregate    # re-print the table from an existing coverage/
make coverage-ratchet      # compare coverage/summary.json to the baseline
make coverage-baseline-update  # record the current numbers as the new floor
```

A single unit, when you do not want to pay for all fourteen toolchains:

```bash
scripts/coverage/run-coverage.sh api-gateway client-app
```

`summary.json` then describes *only* those units, even though `coverage/` still
holds the other directories from a previous run — so a subsequent
`make coverage-ratchet` or `make coverage-baseline-update` cannot act on numbers
this run did not measure. `make coverage-aggregate` re-prints everything
currently in `coverage/` regardless of age, which is why it only prints — it
does not write `summary.json`, so it cannot feed those numbers to the ratchet.

## The ratchet

There is deliberately **no absolute floor**. A repo whose worst unit is at 0% can
only meet a global threshold by setting it near zero, which gates nothing. The
ratchet instead pins each unit to what it measures today: coverage may rise, and
may not fall by more than `tolerance` (0.5pp, to absorb instrumentation jitter
between toolchain patch versions).

- A unit **not in** the baseline is reported as `NEW` and passes. Add it with
  `make coverage-baseline-update`.
- A unit **not in** the summary was not rebuilt by that PR (CI is path-filtered)
  and is ignored.
- A unit that *is* in the summary but produced no number, and had one in the
  baseline, **fails**. Silently losing the measurement is indistinguishable from
  losing all of the coverage.
- A unit with no number **and** no baseline is reported `UNGATED` and passes:
  there is nothing to compare it against, but it does not get to sit there
  unmentioned either. `admin-service` is today's example.
- Lowering a number is allowed but must be explicit: update the baseline in the
  same PR, where a reviewer sees it.

## Recorded baseline

Measured on the WP-00 branch, 2026-08-06, from `main`'s test suites unchanged.
This is the floor in `coverage-baseline.json`.

| Unit | Line coverage | Covered / total | Suite status |
|---|---:|---:|---|
| `report-service` | 81.55% | 610 / 748 | pass |
| `analytics-service` | 80.94% | 1401 / 1731 | pass |
| `document-service` | 78.34% | 528 / 674 | **9 failing** (see findings) |
| `search-service` | 75.42% | 583 / 773 | pass |
| `auth-service` | 73.32% | 316 / 431 | pass |
| `legacy-portal` | 69.80% | 141 / 202 | pass |
| `collab-service` | 65.12% | 308 / 473 | pass |
| `audit-service` | 54.47% | 512 / 940 | pass (24 tests, previously never run) |
| `api-gateway` | 52.44% | 215 / 410 | pass |
| `admin-dashboard` | 40.98% | 159 / 388 | **7 of 64 failing** (see findings) |
| `notification-service` | 27.50% | 209 / 760 | pass |
| `file-service` | 14.50% | 276 / 1903 | pass |
| `client-app` | 0.44% | 7 / 1585 | pass (4 tests) |
| **aggregate** | **47.79%** | **5265 / 11018** | |

`admin-service` is **not** in the baseline: its bundle resolves
`connection_pool 3.0.2`, which does not parse on Ruby 3.3, so RSpec dies while
loading the environment. Its first CI run records it via the ratchet's `NEW`
path.

## Adding a unit

1. Make its suite emit a machine-readable report (`lcov.info`, `coverage.xml`,
   `jacoco.xml`, a Go coverprofile, or SimpleCov's `.last_run.json`).
2. Add one line to `COVERAGE_UNITS` in `scripts/coverage/units.sh`.
3. Add the corresponding `Stage coverage report` + `upload-artifact` steps to its
   CI job, and the job name to `coverage-report`'s `needs:`.
4. Run `make test-coverage && make coverage-baseline-update`.

## Findings from the first measured run

These are pre-existing defects that WP-00 surfaced by removing the suppression.
They are **not** fixed here — WP-00 is a harness package.

1. **`services/audit-service`: 24 xUnit tests had never run.** Both `make test`
   and the CI job invoked a bare `dotnet test` from `services/audit-service`,
   which resolves to `AuditService.csproj` — the web app, not a test project —
   and exits 0 having executed nothing. Naming the test project surfaces 54.47%
   coverage that CI had never seen.
2. **`services/document-service`: 9 of 42 tests fail on `main`.** `19ddab3c`
   ("remove X-User-Id header trust to prevent identity spoofing") made the read
   endpoints require a JWT; `tests/test_documents_api.py` still calls them
   unauthenticated and gets 401. `test_restore_version` then fails with
   `KeyError: -1`. Fixing the tests belongs to WP-06.
3. **`frontend/admin-dashboard`: 7 of 64 specs fail, and CI has never said so.**
   `npm test || true` discards the result. Running it against
   `ChromeHeadlessNoSandbox` — the launcher `karma.conf.js` defines for
   containers, which the npm script's plain `ChromeHeadless` bypasses — gives
   `TOTAL: 7 FAILED, 57 SUCCESS` at 40.98% line coverage. The failures cluster in
   `HealthComponent` (`should load system health data`) and `UsersComponent`
   (`should display page title`, `should apply text filter`, `should load
   users`). WP-16 owns both the failures and the `|| true`.
4. **The Gradle wrapper jars are not in the repo.** `.gitignore` excludes `*.jar`
   repo-wide, so `./gradlew` exists but cannot run in a fresh clone, and
   `notification-service` has no wrapper directory at all. `make test` used
   `./gradlew` and therefore could never have worked outside CI, which installs
   Gradle separately. `scripts/gradle.sh` picks whichever is usable rather than
   committing a binary.

## Known gaps (owned by later work packages)

| Gap | Owner |
|---|---|
| `frontend/client-app`'s `vitest run --passWithNoTests` still cannot fail on an empty suite | WP-14 |
| `admin-dashboard`'s `npm run lint \|\| true` / `npm test \|\| true` in CI | WP-16 |
| `tests/api` is collected but never executed (needs a composed stack) | WP-17 |
| `tests/contract` runs only against a live search-service; `make test` skips it loudly when absent | WP-19 |
| Playwright e2e and Cucumber BDD are not in CI | WP-15, WP-18 |
| `etl`, `clients/windows-desktop`, `demo-platform/dashboard` have no suite to measure | WP-20, WP-22, WP-21 |

`SimpleCov` only writes a percentage to `.last_run.json`, with no line
denominator, so `admin-service` is shown as a percentage and excluded from the
aggregate numerator/denominator. It is still ratcheted.

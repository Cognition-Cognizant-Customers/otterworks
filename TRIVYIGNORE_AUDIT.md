# .trivyignore Audit

Audit of every entry in the root `.trivyignore`, verified against the repo state on `main`
(Trivy v0.71.0, `trivy fs . --severity CRITICAL,HIGH --skip-dirs services/report-service`,
run with and without the ignore file and diffed).

## Headline findings

1. **The `CVE-2021-*` "bulk ignore" wildcard is a no-op.** Trivy's plain `.trivyignore`
   format matches vulnerability IDs exactly and does not expand globs — verified
   empirically: an ignore file containing `CVE-2025-3020*` did **not** suppress
   `CVE-2025-30204` in `services/api-gateway`. Independently, a full-severity scan of the
   monorepo finds **zero** CVE-2021-\* (or CVE-2020-\*) findings anywhere, so even literal
   entries for those years would suppress nothing. The line suppresses nothing and never
   did in its current form.
2. **`frontend/web-app` does not exist on `main`.** It was deleted in commit `a324a50d`
   ("remove native android app build, add capacitor") and replaced by
   **`frontend/client-app`**, a Vite/React (+ Capacitor) app with **no Next.js
   dependency**. The only Next.js in the monorepo is `demo-platform/dashboard`
   (next 15.5.20), which already includes the fixes for every CVE/GHSA in that section.
   All 7 entries in the section suppress nothing.
3. Only **13 of the 31 entries are load-bearing** — removing them reintroduces 15 findings
   (14 CRITICAL/HIGH + 1 MEDIUM). All 13 are kept (see rewritten `.trivyignore`); no
   load-bearing suppression was dropped, so nothing was reintroduced and
   `SECURITY_BACKLOG.md` records no regressions.
4. Section-comment accuracy:
   - **"requires Angular 19+ upgrade"** — holds. `frontend/admin-dashboard/package.json`
     pins `@angular/core ^17.3.0` (lockfile 17.3.12); the fixes ship in 19.2.16+ / 20.x /
     21.x, and no fix exists on the EOL 17.x line.
   - **"requires Rails 7.2+ upgrade"** — holds. `services/admin-service/Gemfile` pins
     `rails ~> 7.1.3` (activestorage 7.1.6 in `Gemfile.lock`); fixes require
     `activestorage >= 7.2.3`. Note the golden-app lab depends on Rails 7.1 behavior
     (planted bug in `config/environments/production.rb`), so the upgrade is deliberately
     deferred.
   - **"etl/airflow: requires Airflow 2.9+ upgrade"** — does not hold. There is no
     `etl/airflow` path and no Airflow dependency anywhere; `etl/` is the *legacy cron*
     pipeline and Airflow is only a **future migration target** per
     `etl/ETL_UPGRADE_GUIDE.md`. All 5 Airflow CVEs suppress nothing.
   - **"Go dependency upgrades require build tool"** — imprecise. The Go toolchain
     (go 1.22) is pinned in `services/api-gateway/go.mod`; the blocking issue is that the
     fixed versions (grpc 1.79.3, otel/sdk 1.40/1.43) require a newer Go toolchain and a
     coordinated module upgrade, while `jwt/v5 5.2.2` alone would be a trivial bump.
   - **"Low-priority npm advisories"** — none apply: lodash is at 4.18.1 (> fix 4.17.21)
     everywhere, and `CVE-2021-33503` is a *Python* urllib3 advisory (fixed 1.26.5;
     repo has urllib3 2.7.0), not npm.
5. Entries suppressing more than their comment claims: `CVE-2026-22610` and
   `CVE-2026-32635` each suppress **two** findings (`@angular/core` *and*
   `@angular/compiler`), still within the admin-dashboard section's scope. No entry
   suppresses findings outside its stated service.

## Entry-by-entry table

| Entry | Section comment | Actually applies to | Verdict | Reason |
|---|---|---|---|---|
| CVE-2024-39877 | etl/airflow (Airflow 2.9+) | nothing | REMOVE | No Airflow anywhere; `etl/` is legacy cron, Airflow is a future target |
| CVE-2024-45034 | etl/airflow | nothing | REMOVE | Same — suppresses no finding |
| CVE-2024-56373 | etl/airflow | nothing | REMOVE | Same — suppresses no finding |
| CVE-2025-54550 | etl/airflow | nothing | REMOVE | Same — suppresses no finding |
| CVE-2025-68675 | etl/airflow | nothing | REMOVE | Same — suppresses no finding |
| CVE-2025-66035 | admin-dashboard (Angular 19+) | @angular/common 17.3.12 (HIGH) | KEEP | Load-bearing; fix requires ≥19.2.16 |
| CVE-2025-66412 | admin-dashboard | @angular/compiler 17.3.12 (HIGH) | KEEP | Load-bearing; fix requires ≥19.2.17 |
| CVE-2026-22610 | admin-dashboard | @angular/compiler **and** @angular/core 17.3.12 (HIGH ×2) | KEEP | Load-bearing; suppresses 2 findings (both in this service) |
| CVE-2026-27970 | admin-dashboard | @angular/core 17.3.12 (HIGH) | KEEP | Load-bearing; fix requires ≥19.2.19 |
| CVE-2026-32635 | admin-dashboard | @angular/compiler **and** @angular/core 17.3.12 (HIGH ×2) | KEEP | Load-bearing; suppresses 2 findings (both in this service) |
| CVE-2025-29927 | frontend/web-app (Next.js) | nothing | REMOVE | `frontend/web-app` deleted (`a324a50d`); replacement `frontend/client-app` is Vite/React, no Next.js; demo-platform's next 15.5.20 already fixed |
| CVE-2024-46982 | frontend/web-app | nothing | REMOVE | Same |
| CVE-2024-51479 | frontend/web-app | nothing | REMOVE | Same |
| GHSA-5j59-xgg2-r9c4 | frontend/web-app | nothing | REMOVE | Same |
| GHSA-h25m-26qc-wcjf | frontend/web-app | nothing | REMOVE | Same |
| GHSA-mwv6-3258-q52c | frontend/web-app | nothing | REMOVE | Same |
| GHSA-q4gf-8mx6-v5v3 | frontend/web-app | nothing | REMOVE | Same |
| CVE-2025-30204 | api-gateway (Go build tool) | golang-jwt/jwt/v5 v5.2.1 (HIGH) | KEEP | Load-bearing; fix 5.2.2 (candidate for a quick standalone bump) |
| CVE-2026-24051 | api-gateway | otel/sdk v1.24.0 (HIGH) | KEEP | Load-bearing; fix 1.40.0 needs newer Go toolchain |
| CVE-2026-39883 | api-gateway | otel/sdk v1.24.0 (HIGH) | KEEP | Load-bearing; fix 1.43.0 needs newer Go toolchain |
| CVE-2026-33186 | api-gateway | google.golang.org/grpc v1.61.1 (CRITICAL) | KEEP | Load-bearing; fix 1.79.3 needs newer Go toolchain |
| CVE-2026-33195 | admin-service (Rails 7.2+) | activestorage 7.1.6 (CRITICAL) | KEEP | Load-bearing; fix ≥7.2.3; Rails 7.1 pinned for golden-app lab |
| CVE-2026-33658 | admin-service | activestorage 7.1.6 (MEDIUM) | KEEP | Load-bearing at MEDIUM (below CI's CRITICAL/HIGH gate but still matched) |
| CVE-2026-0994 | document-service (poetry) | protobuf 4.25.9 (HIGH) | KEEP | Load-bearing; fix 5.29.6 |
| CVE-2024-53981 | document-service | nothing | REMOVE | python-multipart is at 0.0.32, already past fix 0.0.18 — remediated |
| CVE-2026-24486 | document-service | nothing | REMOVE | No matching finding in any lockfile in the repo |
| CVE-2024-47874 | document-service | starlette 0.37.2 (HIGH) | KEEP | Load-bearing; fix 0.40.0 |
| CVE-2021-\* | "Bulk ignore — revisit in Q4" | nothing | REMOVE | Wildcards are not expanded by Trivy's plain ignore format (verified empirically), and no CVE-2021 finding exists at any severity |
| CVE-2021-23337 | Low-priority npm | nothing | REMOVE | lodash 4.18.1 in all lockfiles > fix 4.17.21 — remediated |
| CVE-2021-33503 | Low-priority npm | nothing | REMOVE | Python urllib3 advisory (mislabeled npm); repo has urllib3 2.7.0 > fix 1.26.5 |
| CVE-2020-28500 | Low-priority npm | nothing | REMOVE | lodash 4.18.1 > fix 4.17.21 — remediated |

## Scan evidence

- With the current `.trivyignore`: 38 CRITICAL/HIGH findings remain (baseline that CI
  diffs against; the workflow only gates *newly introduced* findings).
- Without any ignore file: 52 CRITICAL/HIGH findings — the file suppresses exactly
  **14 CRITICAL/HIGH** (7 Angular, 4 api-gateway Go, 1 activestorage CRITICAL,
  2 document-service Python) plus **1 MEDIUM** (activestorage CVE-2026-33658).
- The rewritten `.trivyignore` (KEEP entries only) suppresses the identical set — the
  CRITICAL/HIGH result diff before/after the rewrite is empty.

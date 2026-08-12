# Deferred Security Findings — frontend/admin-dashboard

Findings from the 2026-08 `make security-scan` run (Trivy fs, HIGH/CRITICAL) against
Angular 17.3.12. None of these CVEs has a fixed release on the 17.x line — the earliest
fixed versions are 19.2.23+ / 20.x / 21.x / 22.x, so remediation requires a major
(breaking) Angular framework upgrade to 19+. Per policy these are deferred, not
suppressed: they are NOT added to `.trivyignore` and will continue to be reported
until the Angular 19+ upgrade lands (the same upgrade already pending for the 7
previously accepted Angular CVEs).

| Finding | Package | Installed | Severity | Earliest fixed versions | Status |
|---|---|---|---|---|---|
| CVE-2026-50170 | @angular/common | 17.3.12 | High | 19.2.23, 20.3.22, 21.2.15, 22.0.0-rc.2 | DEFERRED — requires Angular 19+ major upgrade |
| CVE-2026-50171 | @angular/common | 17.3.12 | High | 19.2.23, 20.3.22, 21.2.15, 22.0.0-rc.2 | DEFERRED — requires Angular 19+ major upgrade |
| CVE-2026-54266 | @angular/common | 17.3.12 | High | 20.3.25, 21.2.17, 22.0.1 | DEFERRED — requires Angular 20+ major upgrade |
| CVE-2026-54268 | @angular/common | 17.3.12 | High | 20.3.25, 21.2.17, 22.0.1 | DEFERRED — requires Angular 20+ major upgrade |
| CVE-2026-68945 | @angular/common | 17.3.12 | High | 20.3.27, 21.2.19, 22.0.2 | DEFERRED — requires Angular 20+ major upgrade |
| CVE-2026-69151 | @angular/compiler | 17.3.12 | High | 20.3.27, 21.2.19, 22.0.1 | DEFERRED — requires Angular 20+ major upgrade |
| CVE-2026-54267 | @angular/core | 17.3.12 | High | 20.3.25, 21.2.17, 22.0.1 | DEFERRED — requires Angular 20+ major upgrade |
| CVE-2026-69151 | @angular/core | 17.3.12 | High | 20.3.27, 21.2.19, 22.0.1 | DEFERRED — requires Angular 20+ major upgrade |

## Exposure notes (why deferral is acceptable in the interim)

- The `HttpTransferCache` CVEs (CVE-2026-50170, CVE-2026-54266, CVE-2026-68945,
  CVE-2026-54267) affect SSR client hydration (`provideClientHydration` /
  `withHttpTransferCache`). This dashboard is a purely client-side rendered SPA with no
  SSR and no hydration transfer cache, so the vulnerable code paths are not exercised.
- The i18n XSS (CVE-2026-69151) and the DoS issues in `DecimalPipe`/date formatting
  (CVE-2026-50171, CVE-2026-54268) only process developer-controlled format strings in
  this app; no user-supplied format strings are passed to these APIs.

These notes reduce practical risk but do not fix the findings; the Angular 19+ upgrade
remains the remediation.

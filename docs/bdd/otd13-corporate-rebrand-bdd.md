# OTD-13 — OtterWorks Corporate Rebrand: BDD Requirements

Story: OTD-13 "OtterWorks corporate rebrand: classic enterprise UI, otter logo & company identity"

Display-layer rebrand only. Interview resolutions honored: Playwright e2e specs updated to new
landing copy; optimized PNG `<img>` Logo component + favicons; footer on marketing pages AND app
shell with fictional Terms/Privacy static pages; admin dashboard = theme alignment only; sidebar
groups WORKSPACE / COLLABORATION / SYSTEM.

## BDD-01: Otter logo replaces "OW" block on landing
**Traces to:** AC-01   **Category:** UI
**Given** I am on the landing page (`/`)
**When** the hero renders
**Then** the otter logo image (`img[alt="OtterWorks logo"]`) is visible and no "OW" text block exists
### Testing Flow
1. Navigate to http://localhost:3000/
2. Verify otter logo `<img>` in the hero/top bar; verify no "OW" tile

## BDD-02: Otter logo on login and register
**Traces to:** AC-01   **Category:** UI
**Given** I am on `/login` (then `/register`)
**When** the page renders
**Then** the otter logo image is shown above the form instead of the "OW" tile
### Testing Flow
1. Navigate to /login → verify logo img; 2. Navigate to /register → verify logo img

## BDD-03: Otter logo in app sidebar + updated favicon
**Traces to:** AC-01   **Category:** UI
**Given** I am logged in
**When** the app shell renders
**Then** the sidebar header shows the otter logo image, and the document favicon links point to the otter PNG/ICO set
### Testing Flow
1. Log in with the seeded drive account; 2. Verify sidebar logo img; 3. Verify browser tab favicon (index.html links favicon.ico / favicon-32.png / favicon-16.png / apple-touch-icon.png)

## BDD-04: Classic enterprise palette and typography
**Traces to:** AC-02a   **Category:** UI
**Given** any app page
**When** it renders
**Then** primary chrome uses navy/steel (#1F3A5F family) on white/#F4F5F7 surfaces, square corners (≤2px), no gradients, 13–14px system sans (Segoe UI/Helvetica/Arial) body text
### Testing Flow
1. Open /dashboard; 2. Inspect sidebar/top bar colors, button corners, body font-size/family

## BDD-05: Dense top bar, grouped ALL-CAPS sidebar, breadcrumbs
**Traces to:** AC-02b   **Category:** UI
**Given** I am logged in
**When** the app shell renders
**Then** the top utility bar shows logo-left context, global search, and help/notification/user cluster right; the sidebar nav is grouped under ALL-CAPS section labels WORKSPACE, COLLABORATION, SYSTEM; breadcrumbs render on drill-down pages (e.g. Files → folder)
### Testing Flow
1. Log in; 2. Verify sidebar group labels; 3. Verify top bar layout; 4. Open a folder in /files and verify breadcrumb

## BDD-06: Corporate footer on marketing pages and app shell
**Traces to:** AC-02c   **Category:** UI
**Given** the landing page and any logged-in app page
**When** each renders
**Then** a footer shows "© OtterWorks, Inc. · v0.1.0 · Terms · Privacy" with working links
### Testing Flow
1. Verify footer on /; 2. Log in and verify footer in the app shell; 3. Click Terms → /terms; 4. Click Privacy → /privacy (both fictional static pages)

## BDD-07: Landing presents full fictional corporate identity
**Traces to:** AC-03   **Category:** FUNC
**Given** I am on the landing page
**When** I scroll through it
**Then** I see the founding story, leadership team, department structure, products overview, press releases, and careers sections — all clearly fictional, no real-person info
### Testing Flow
1. Navigate to /; 2. Verify sections: Our Story, Leadership, Departments, Products, Press / Newsroom, Careers; 3. Confirm content is fictional

## BDD-08: Admin dashboard branding aligned
**Traces to:** AC-04   **Category:** UI
**Given** the admin dashboard at :4200
**When** I view the login page and sign in (mock auth: any email + non-empty password)
**Then** login and sidebar show the otter logo (not the "pets" icon) with a navy theme and muted amber accent, consistent with the web app
### Testing Flow
1. Open http://localhost:4200; 2. Verify otter logo + navy/amber login; 3. Sign in; 4. Verify sidebar logo/theme

## BDD-09: Existing routes and flows unchanged
**Traces to:** AC-05   **Category:** NAV
**Given** the rebranded app
**When** I log in with the seeded account and navigate login → dashboard → files (browse a folder) → documents (open a document)
**Then** every existing route works with real seeded data; only /terms and /privacy were added
### Testing Flow
1. Log in via ${DRIVE_EMAIL}; 2. /dashboard loads stats; 3. /files lists department folders, open one; 4. /documents open a document in the editor

## BDD-10: Quality gates and test expectations
**Traces to:** AC-06a, AC-06b   **Category:** ERR
**Given** the rebrand branch
**When** `make lint` and `make test` run (plus client-app unit/BDD/e2e suites)
**Then** they pass; the diff is confined to frontend (+ docs/tests); no planted bug is touched; admin "OtterWorks" spec assertions remain intact; landing e2e/BDD specs are intentionally updated to the new copy
### Testing Flow
1. Run make lint, make test; 2. Run client-app `npm test`, `npm run test:bdd`, `npm run test:e2e`; 3. `git diff --stat main` confined to frontend/docs

## AC → BDD Traceability Matrix

| AC-ID | Category | AC Title | BDD Scenario(s) | Status |
|-------|----------|----------|-----------------|--------|
| AC-01 | UI | Otter logo replaces "OW" blocks + favicon | BDD-01, BDD-02, BDD-03 | Mapped |
| AC-02a | UI | Navy/steel palette, square corners, no gradients, system sans | BDD-04 | Mapped |
| AC-02b | UI | Dense top bar + grouped ALL-CAPS sidebar + breadcrumbs | BDD-05 | Mapped |
| AC-02c | UI | Footer "© OtterWorks, Inc. · v0.1.0 · Terms · Privacy" on all pages | BDD-06 | Mapped |
| AC-03 | FUNC | Landing shows full fictional corporate identity | BDD-07 | Mapped |
| AC-04 | UI | Admin login + sidebar: otter logo, navy theme, amber accent | BDD-08 | Mapped |
| AC-05 | NAV | All existing routes/flows unchanged (additive /terms, /privacy) | BDD-09 | Mapped |
| AC-06a | ERR | Lint/test pass; no planted bug; diff confined to frontend | BDD-10 | Mapped |
| AC-06b | ERR | Admin "OtterWorks" assertions intact; e2e updated intentionally | BDD-10 | Mapped |

### Coverage Summary
- Total AC: 9 · Total BDD: 10 · Categories: FUNC(1) UI(6) NAV(1) ERR(1)
- Unmapped AC-IDs: NONE

## Data Dependencies
- No new DB tables or API endpoints — display layer only.
- Footer version: `frontend/client-app/package.json` `version` (0.1.0) injected via Vite `define` (`__APP_VERSION__`).
- Seeded RetailCo enterprise drive data (real Postgres/S3 via gateway) used for BDD-09 route verification.
- Corporate identity content: static fictional constants in `frontend/client-app/src/lib/corporate.ts`.
- Logo asset: optimized PNG derived from the provided otter mark → `src/assets/otter-logo.png` + favicon set in `public/`.

## Recovered-spec note
The Stage-1 `technical-spec.md` attachment URL returned 401 in this session; the approved AC matrix
and interview resolutions were recovered verbatim from the planning session and are reflected above.

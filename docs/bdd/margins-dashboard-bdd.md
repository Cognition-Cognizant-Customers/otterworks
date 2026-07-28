# BDD Requirements — Margins Analytics Dashboard (OTD-15)

Derived from the approved OTD-15 technical spec AC Matrix (AC-01…AC-15).
Feature: `/margins` dashboard in the React web app, backed by the Scala/Akka
analytics-service (schema `analytics` in Postgres), with a deterministic
synthetic market baseline and an authenticated ingestion endpoint for manual
Trading Economics pulls.

---

## BDD-01: Migration creates market/margin tables and seeds deterministic baseline
**Traces to:** AC-01   **Category:** FUNC
**Given** a fresh Postgres database
**When** the analytics-service boots (Flyway V2 + `MarketSeeder`)
**Then** schema `analytics` contains `market_series`, `market_prices`, `products`, `product_margin_daily`, `sync_runs`; all 7 series, ~40 SKUs, baseline prices + seeded random-walk extension to today are populated; `product_margin_daily` is computed for every SKU; a second boot inserts no new rows (idempotent)
### Testing Flow
1. Boot service against fresh DB (testcontainers) → assert tables + row counts
2. Boot again → assert identical row counts

## BDD-02: Flyway isolation fix activates the durable Postgres store
**Traces to:** AC-02   **Category:** FUNC
**Given** a seeded stack where auth-service owns `public.flyway_schema_history`
**When** analytics-service boots
**Then** analytics migrations apply into schema `analytics` with history table `flyway_schema_history_analytics`; no checksum-mismatch fallback; log contains "durable PostgreSQL metrics store"; existing event endpoints keep working
### Testing Flow
1. `docker logs otterworks-analytics-service` → assert durable-store log, no fallback warning
2. `GET /api/v1/analytics/dashboard` with JWT → 200

## BDD-03: GET margins returns KPIs and per-SKU rows through the gateway
**Traces to:** AC-03   **Category:** FUNC
**Given** the seeded stack and a valid JWT
**When** `GET /api/v1/analytics/margins` via the gateway (:8080)
**Then** 200 with `as_of_date`, `source`, `kpis` (gross_margin_pct, avg_cogs_usd, salmon_index, freight_index) and ~40 rows whose `margin_pct` matches the locked margin formula
### Testing Flow
1. Login via `/api/v1/auth/login` → JWT
2. curl the endpoint → assert shape + row count + spot-check formula

## BDD-04: GET series/status endpoints return filtered ordered data
**Traces to:** AC-04   **Category:** FUNC
**Given** seeded data
**When** `GET .../margins/series?sku=…&from=…&to=…`, `.../market/series`, `.../market/prices?series_code=…`, `.../market/status`
**Then** 200; series rows are date-filtered and date-ordered; status reflects the latest `sync_runs` row
### Testing Flow
1. curl each endpoint with JWT → assert ordering, bounds, status fields

## BDD-05: POST observations ingests and recomputes margins
**Traces to:** AC-05   **Category:** FUNC
**Given** a valid JWT and known series codes
**When** `POST /api/v1/analytics/market/observations` with valid observations
**Then** 200 `{accepted, rejected: [], recomputed_skus, run_id}`; `market_prices` upserted with `source='manual_pull'`; affected SKUs' `product_margin_daily` recomputed; `sync_runs` gains a `manual_pull` row; `/market/status` (and the UI badge) flips to live
### Testing Flow
1. POST 3 observations (salmon, FX, freight) → assert response counts
2. GET /margins → as-of/source updated; DB rows show manual_pull

## BDD-06: POST rejects invalid observations item-by-item
**Traces to:** AC-06   **Category:** ERR
**Given** a valid JWT
**When** POST contains an unknown `series_code`, a malformed date, or `value <= 0`
**Then** all-invalid → 400 with itemized `rejected[]`; mixed → 200 with valid items accepted and invalid ones listed in `rejected[]`; no partial commit per item
### Testing Flow
1. POST all-invalid payload → 400 + rejected reasons
2. POST mixed payload → 200, accepted=valid count, rejected lists the bad item

## BDD-07: All new endpoints require gateway JWT
**Traces to:** AC-07   **Category:** RBAC
**Given** no (or an invalid) Authorization header
**When** any new GET/POST endpoint is called via the gateway
**Then** 401 from the gateway; nothing reaches analytics-service
### Testing Flow
1. curl each new endpoint without a token → 401

## BDD-08: /margins renders the full enterprise dashboard
**Traces to:** AC-08   **Category:** UI
**Given** a logged-in user and the seeded stack
**When** `/margins` is opened at :3000
**Then** the page shows 4 KPI tiles (Gross Margin %, COGS / unit, Salmon Index, Freight Index), the SKU margins grid, commodity + margin recharts line charts with gridlines and plain legend, the caption "Data as of <ts> — Source: Trading Economics (manual pull)", a synthetic/live source badge, and an Export CSV button — all from real API data
### Testing Flow
1. Login → click Margins → verify every element and that numbers match the API

## BDD-09: SKU grid sorts and filters client-side
**Traces to:** AC-09   **Category:** UI
**Given** the rendered grid
**When** a column header is clicked / text typed into the filter box / a category picked
**Then** rows re-sort (toggling asc/desc) and filter accordingly
### Testing Flow
1. Click "Margin %" header → order flips; type a SKU fragment → rows narrow; pick category → only that category remains

## BDD-10: Export CSV downloads backend CSV matching the grid
**Traces to:** AC-10   **Category:** UI
**Given** the rendered grid
**When** the Export CSV button is clicked
**Then** the browser downloads a CSV served by `GET /api/v1/analytics/margins/export?format=csv` whose header and rows match the grid data
### Testing Flow
1. Click Export CSV → capture download → assert header row + SKU present

## BDD-11: Loading and error states are handled
**Traces to:** AC-11   **Category:** ERR/UI
**Given** a slow or failing margins API
**When** `/margins` loads
**Then** a skeleton/spinner shows while loading and a friendly error state (no blank crash) shows on failure
### Testing Flow
1. Playwright: block `**/api/v1/analytics/margins*` → error state visible
2. Normal load → skeleton appears then content

## BDD-12: Sidebar navigation entry works and existing routes are unaffected
**Traces to:** AC-12   **Category:** NAV
**Given** a logged-in user
**When** "Margins" is clicked in the sidebar; navigating away and back; loading `/margins` directly
**Then** `/margins` is active-highlighted; direct URL load works; all pre-existing routes still render
### Testing Flow
1. Sidebar walk: Dashboard → Margins → Files → back/forward → direct URL

## BDD-13: Demo-safe default — synthetic data only
**Traces to:** AC-13   **Category:** ERR
**Given** a fresh stack with zero POSTed observations
**When** `/margins` is opened
**Then** the dashboard is fully populated from synthetic data and the badge reads "synthetic"
### Testing Flow
1. On fresh seed (no manual pulls) open /margins → populated + synthetic badge

## BDD-14: Shared CSV contract exists and copies stay in lockstep
**Traces to:** AC-14   **Category:** FUNC
**Given** a repo checkout
**When** `testdata/market-series/` is inspected and `sbt test` runs
**Then** `series.csv`, `baseline_prices.csv`, `products.csv`, `README.md` exist with the documented schemas, and a unit test asserts the bundled analytics-service resource copies are checksum-identical
### Testing Flow
1. ls testdata/market-series → 4 files; run the equality spec → green

## BDD-15: Lint/test green with no collateral changes
**Traces to:** AC-15   **Category:** Regression
**Given** the full branch diff
**When** `make lint` / `make test` / CI run
**Then** green; no new service/infra; admin-service planted bug untouched
### Testing Flow
1. Run lint/test for touched components; check `git diff --stat` scope; CI green

---

## AC → BDD Traceability Matrix

| AC-ID | Category | AC Title | BDD Scenario(s) | Status |
|-------|----------|----------|-----------------|--------|
| AC-01 | FUNC | Migration + deterministic baseline seed | BDD-01 | Mapped |
| AC-02 | FUNC | Flyway isolation fix → durable store | BDD-02 | Mapped |
| AC-03 | FUNC | GET margins through gateway | BDD-03 | Mapped |
| AC-04 | FUNC | GET series endpoints | BDD-04 | Mapped |
| AC-05 | FUNC | POST observations ingests + recomputes | BDD-05 | Mapped |
| AC-06 | ERR | POST rejects invalid observations | BDD-06 | Mapped |
| AC-07 | RBAC | Endpoints require auth | BDD-07 | Mapped |
| AC-08 | UI | /margins renders full dashboard | BDD-08 | Mapped |
| AC-09 | UI | Grid sort + filter | BDD-09 | Mapped |
| AC-10 | UI | CSV export | BDD-10 | Mapped |
| AC-11 | UI | Loading / error states | BDD-11 | Mapped |
| AC-12 | NAV | Sidebar entry + routes unaffected | BDD-12 | Mapped |
| AC-13 | ERR | Demo-safe synthetic default | BDD-13 | Mapped |
| AC-14 | FUNC | Shared CSV contract | BDD-14 | Mapped |
| AC-15 | Regression | Lint/test green, no collateral | BDD-15 | Mapped |

### Coverage Summary
- Total AC: 15 · Total BDD: 15 · Categories: FUNC(6) UI(3) NAV(1) ERR(3) RBAC(1) Regression(1)
- Unmapped AC-IDs: NONE

## Data Dependencies
- **Tables (schema `analytics`, migration `V2__create_market_margin_tables.sql`):** `market_series`, `market_prices`, `products`, `product_margin_daily`, `sync_runs` (+ existing `analytics_events`, `analytics_daily_metrics` relocated into schema `analytics` by the approved Flyway fix).
- **Seed contract:** `testdata/market-series/{series.csv, baseline_prices.csv, products.csv, README.md}` = bundled copies in `services/analytics-service/src/main/resources/seed/market-series/` (checksum-equality spec keeps them in lockstep).
- **Endpoints (all behind gateway JWT):** `GET /api/v1/analytics/margins`, `GET .../margins/series`, `GET .../margins/export?format=csv`, `GET .../market/series`, `GET .../market/prices`, `GET .../market/status`, `POST .../market/observations`.
- **Component → service → endpoint:** `pages/margins.tsx` → `marginsApi` (`src/lib/api.ts`, axios `apiClient`, same-origin `/api/v1` proxy) → api-gateway :8080 (JWT) → analytics-service :8088 → Postgres schema `analytics`.
- **Margin model:** `commodity_cost = price(native) × fx_to_usd × content_kg` (NOK ÷ USD_NOK); `freight_cost = (WCI_USD_FEU / 25000 kg) × freight_kg`; `cogs = (commodity + freight) × (1 + overhead_pct/100)`; `margin_pct = (list_price − cogs) / list_price × 100`.

# OTD-14 — OtterWorks Multimodal Seed Drive: BDD Requirements

Feature: upgrade the pre-seeded enterprise drive (`testdata/generated/retail-drive/`)
to OtterWorks-themed multimodal content driven by the shared ~40-SKU catalog in
`testdata/market-series/` (owned by OTD-15).

## BDD-01: OtterWorks theme end-to-end, zero RetailCo remnants
**Traces to:** AC-01   **Category:** FUNC
**Given** a fresh stack reseeded with the new generator
**When** the drive is browsed and the seed sources are grepped
**Then** all folder names, suppliers, campaigns, doc/file text are OtterWorks (otter-retail) themed and `grep -ri retailco` over seed sources and generated content (excluding the login email value) yields 0 hits
### Testing Flow
1. `grep -riI retailco testdata/generated/retail-drive --exclude-dir=__pycache__` → only the login-email fallback (none expected in content strings)
2. Browser: log in at :3000, open department folders, open a docx/doc → text says OtterWorks

## BDD-02: Shared catalog drives all figures consistently
**Traces to:** AC-02   **Category:** FUNC
**Given** `testdata/market-series/` CSVs are present
**When** two different artifacts referencing the same SKU are generated
**Then** both show identical list price / COGS / margin from `catalog.py`; no `_PRODUCTS` or `uniform()` money paths remain
### Testing Flow
1. pytest: generate xlsx + csv for the same SKU/date, parse, assert identical figures
2. Code review/grep: `_PRODUCTS` removed; no `uniform(` in financial paths

## BDD-03: Chart PNGs seeded and previewable
**Traces to:** AC-03a, AC-03f   **Category:** FUNC/UI
**Given** matplotlib is installed and the drive is seeded
**When** the user opens a margin-trend / salmon-price chart PNG in Analytics & Insights or Finance
**Then** the file exists and renders inline as an image in file-detail
### Testing Flow
1. Browser: Analytics & Insights → Market Charts → open a chart PNG → `<img>` preview renders

## BDD-04: Rich PDFs (contracts / spec sheets / invoices)
**Traces to:** AC-03b, AC-03f   **Category:** FUNC/UI
**Given** the drive is seeded
**When** the user opens a supplier contract / spec sheet / invoice PDF
**Then** the PDF renders in the embedded viewer with multi-section content, tables and embedded images, catalog-consistent figures
### Testing Flow
1. Browser: Procurement/Legal/Finance → open a contract or invoice PDF → embedded PDF viewer shows tables/images

## BDD-05: Image-bearing pptx decks
**Traces to:** AC-03c   **Category:** FUNC
**Given** the drive is seeded
**When** a pptx deck is downloaded and inspected
**Then** it contains embedded chart/product images (`add_picture` media parts)
### Testing Flow
1. Download a deck via API; unzip; assert `ppt/media/*.png` present
2. Browser: pptx shows icon + download (no inline preview — expected)

## BDD-06: Per-SKU product art (PNG/SVG)
**Traces to:** AC-03d, AC-03f   **Category:** FUNC/UI
**Given** the drive is seeded
**When** the user opens product-art files in Marketing / E-Commerce
**Then** PNG and SVG assets exist per SKU and render inline as images
### Testing Flow
1. Browser: Marketing → Product Art → open a PNG and an SVG → `<img>` preview renders

## BDD-07: MP4s seeded and playable
**Traces to:** AC-03e, AC-03f   **Category:** FUNC/UI
**Given** 2–5 tiny committed MP4 clips in `testdata/generated/retail-drive/assets/`
**When** the user opens an MP4 in Marketing/E-Commerce
**Then** the file has mime `video/mp4` and file-detail shows a `<video>` player that plays
### Testing Flow
1. Browser: Marketing → Campaign Videos → open MP4 → video element renders/plays

## BDD-08: Series reusable by analytics (shared contract)
**Traces to:** AC-04   **Category:** FUNC
**Given** OTD-15's committed baseline + documented deterministic walk
**When** `catalog.py` derives a price/margin for any date
**Then** it consumes `testdata/market-series/` CSVs verbatim, reimplements the documented `java.util.Random` walk (seed = `series_code.hashCode ^ epochDay`), and repeated calls return identical values; the contract is documented in the README
### Testing Flow
1. pytest: `price_on(series, d)` twice → identical; matches the raw baseline CSV value for an in-baseline date
2. README review: contract + margin model documented

## BDD-09: Fresh-stack seeding succeeds within size bounds
**Traces to:** AC-05a   **Category:** FUNC/PERF
**Given** a fresh local stack (`make infra-up && make up`) with an empty drive
**When** `generate_drive.py --register --departments all` runs
**Then** it completes without errors, ~2.5k files, each file <5 MB, corpus ≤ a few hundred MB
### Testing Flow
1. Run generator against local gateway; capture per-department counts
2. `awslocal s3 ls s3://otterworks-files --recursive --summarize` → size check

## BDD-10: Idempotent re-run
**Traces to:** AC-05b   **Category:** FUNC
**Given** an already-seeded drive
**When** the generator runs again with the same arguments
**Then** 0 new file uploads and 0 new docs per department (skips by folder+filename / title)
### Testing Flow
1. Re-run generator; assert `files=0 docs=0` in every department line

## BDD-11: Lint/tests green, seed-only diff, planted bugs untouched
**Traces to:** AC-06a, AC-06b   **Category:** Regression
**Given** the change set
**When** `make lint` / relevant tests run and the diff is reviewed
**Then** they pass; diff limited to `testdata/` (+ `docs/bdd/`); `services/`, `frontend/` untouched; planted bugs untouched
### Testing Flow
1. `git diff --stat main` → only testdata/ + docs/bdd paths
2. `make lint` relevant components pass; seed pytest green

## BDD-12: Drive browsing unaffected
**Traces to:** AC-07   **Category:** NAV
**Given** the reseeded drive
**When** the user navigates folders → file detail → back, and refreshes
**Then** navigation and pagination behave as before (content-only change)
### Testing Flow
1. Browser: dashboard → folder → subfolder → file → back → refresh; no errors

## BDD-13: Missing market-series CSVs fail fast
**Traces to:** AC-08   **Category:** ERR
**Given** `testdata/market-series/` is absent
**When** the generator (or catalog import) runs
**Then** it fails immediately with an actionable error naming the missing path and OTD-15 ownership — no silent random fallback
### Testing Flow
1. pytest: point loader at a nonexistent dir → assert `MarketSeriesMissingError` with actionable message

## AC → BDD Traceability Matrix

| AC-ID | Category | AC Title | BDD Scenario(s) | Status |
|-------|----------|----------|-----------------|--------|
| AC-01 | FUNC | OtterWorks theme end-to-end | BDD-01 | Mapped |
| AC-02 | FUNC | Shared catalog drives all figures | BDD-02 | Mapped |
| AC-03a | FUNC | Chart PNGs seeded | BDD-03 | Mapped |
| AC-03b | FUNC | Rich PDFs seeded | BDD-04 | Mapped |
| AC-03c | FUNC | Image-bearing pptx seeded | BDD-05 | Mapped |
| AC-03d | FUNC | Product art seeded | BDD-06 | Mapped |
| AC-03e | FUNC | MP4s seeded & previewable | BDD-07 | Mapped |
| AC-03f | UI | Multimodal preview in web app | BDD-03, BDD-04, BDD-06, BDD-07 | Mapped |
| AC-04 | FUNC | Series reusable by analytics | BDD-08 | Mapped |
| AC-05a | FUNC | Fresh-stack seeding succeeds | BDD-09 | Mapped |
| AC-05b | FUNC | Idempotent re-run | BDD-10 | Mapped |
| AC-06a | Regression | Lint/tests green | BDD-11 | Mapped |
| AC-06b | Regression | Seed-only diff, no planted-bug fixes | BDD-11 | Mapped |
| AC-07 | NAV | Drive browsing unaffected | BDD-12 | Mapped |
| AC-08 | ERR | Missing market-series CSVs | BDD-13 | Mapped |

### Coverage Summary
- Total AC: 15 · Total BDD: 13 · Categories: FUNC(10) UI(1) NAV(1) ERR(1) Regression(2) PERF(inside BDD-09)
- Unmapped AC-IDs: NONE

## Data Dependencies
- Inputs: `testdata/market-series/series.csv`, `baseline_prices.csv` (daily, 2024-08-01…2026-06-30), `products.csv` (~40 SKUs) — OTD-15 contract, read-only
- Endpoints: `POST/GET /api/v1/folders` (DynamoDB), `POST /api/v1/files/upload` + `GET /api/v1/files` (S3+DynamoDB via LocalStack), `POST/GET /api/v1/documents` (Postgres)
- Flow: `catalog.py` → `taxonomy.py` → `filegen.py` → `generate_drive.py` → API gateway :8080 → file-service / document-service
- Preview surface: `frontend/client-app/src/pages/file-detail.tsx` (image/*, video/*, application/pdf, text/*) — unchanged

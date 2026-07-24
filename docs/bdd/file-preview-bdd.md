# BDD Requirements: File Preview for All File Types (OTD-12)

Derived from the approved AC Matrix in the Stage 1 implementation plan.
Feature slug: `file-preview`. Branch: `workshop-file-preview-all-types`.

## Data Dependencies

| Item | Source |
|---|---|
| File metadata (`name`, `mime_type`, `size_bytes`, `s3_key`) | DynamoDB `otterworks-file-metadata` via `GET /api/v1/files/{id}` (file-service) |
| File bytes | S3 `otterworks-files` via presigned URL from `GET /api/v1/files/{id}/download` (file-service; presign now sets `response-content-type` from stored `mime_type`) |
| Component → service → endpoint | `pages/file-detail.tsx` → `filesApi.get` / `filesApi.getDownloadUrl` (`src/lib/api.ts`) → API gateway `:8080` → file-service `:8082` |
| Seeded data | RetailCo drive (2,445 files: xlsx/pdf/docx/csv/pptx/png/json/md/jpeg; mp4+mp3 added by this change to `testdata/generated/retail-drive`) |

---

## BDD-01: Open preview from the file list
**Traces to:** AC-01  **Category:** FUNC
**Given** a logged-in user on `/files` with seeded files
**When** they click a file card
**Then** the file detail page (`/files/{id}`) opens showing a "Preview" panel, and no file download is triggered
### Testing Flow
1. Log in, navigate to `/files`, open a department folder. 2. Click a file card. 3. Verify the Preview panel header and rendered content; verify no download occurred.

## BDD-02: Image renders inline
**Traces to:** AC-02  **Category:** FUNC
**Given** a seeded png/jpeg file
**When** its detail page loads
**Then** the image is displayed via `<img>` sourced from the presigned URL
### Testing Flow
1. Open a seeded `.png` file's detail page. 2. Verify the image is visible in the Preview panel.

## BDD-03: PDF renders inline (including seeded files)
**Traces to:** AC-03  **Category:** FUNC
**Given** a seeded PDF whose S3 object was stored as `binary/octet-stream`
**When** its detail page loads
**Then** the PDF renders inside the preview iframe (presign overrides `response-content-type=application/pdf`)
### Testing Flow
1. Open a seeded `.pdf` file's detail page. 2. Verify the PDF viewer renders content in the iframe (not a forced download).

## BDD-04: Text/code renders with line numbers
**Traces to:** AC-04  **Category:** FUNC
**Given** a seeded text/markdown/json file
**When** its detail page loads
**Then** a line-numbered text preview shows the real file content (500 KB truncation behavior preserved)
### Testing Flow
1. Open a seeded `.md` or `.json` file. 2. Verify line-numbered content matches the real file.

## BDD-05: Spreadsheet (xlsx) renders as a table
**Traces to:** AC-05  **Category:** FUNC
**Given** a seeded `.xlsx` file
**When** its detail page loads
**Then** the first sheet renders as an HTML table; multi-sheet files expose sheet tabs
### Testing Flow
1. Open a seeded `.xlsx` file. 2. Verify a data table renders with headers/rows from the real workbook; switch sheet tab if present.

## BDD-06: CSV renders as a table
**Traces to:** AC-06  **Category:** FUNC
**Given** a seeded `.csv` file
**When** its detail page loads
**Then** the CSV renders as a table (not raw text)
### Testing Flow
1. Open a seeded `.csv` file. 2. Verify tabular rendering with the file's real columns.

## BDD-07: Word document renders inline
**Traces to:** AC-07  **Category:** FUNC
**Given** a seeded `.docx` file
**When** its detail page loads
**Then** the document body renders via docx-preview
### Testing Flow
1. Open a seeded `.docx` file. 2. Verify formatted document content is visible.

## BDD-08: Video plays inline
**Traces to:** AC-08  **Category:** FUNC
**Given** an `.mp4` file (from the updated seed or uploaded via the real upload flow)
**When** its detail page loads
**Then** a `<video controls>` player renders and is playable
### Testing Flow
1. Open an `.mp4` file. 2. Verify the video player renders with controls.

## BDD-09: Audio plays inline
**Traces to:** AC-09  **Category:** FUNC
**Given** an `.mp3` file (from the updated seed or uploaded via the real upload flow)
**When** its detail page loads
**Then** an `<audio controls>` player renders and is playable
### Testing Flow
1. Open an `.mp3` file. 2. Verify the audio player renders with controls.

## BDD-10: Graceful fallback for unsupported types
**Traces to:** AC-10  **Category:** FUNC
**Given** a seeded `.pptx` (or unknown mime) file
**When** its detail page loads
**Then** a clear "no inline preview" message with a Download option is shown; the page does not crash
### Testing Flow
1. Open a seeded `.pptx` file. 2. Verify the fallback message and working Download button.

## BDD-11: Seed includes video/audio files
**Traces to:** AC-11  **Category:** FUNC
**Given** a freshly seeded stack (retail-drive generator)
**When** the drive is browsed / files API queried
**Then** at least one `.mp4` and one `.mp3` exist in the seeded drive
### Testing Flow
1. Run/inspect the seed generator output (or query `GET /api/v1/files`). 2. Verify mp4 and mp3 entries exist with `video/mp4` / `audio/mpeg` mime types.

## BDD-12: Loading state while preview prepares
**Traces to:** AC-12  **Category:** UI
**Given** any previewable file
**When** the presigned URL / bytes are still loading
**Then** a spinner is shown until the renderer mounts
### Testing Flow
1. Open a file detail page (throttle or observe initial load). 2. Verify the spinner appears before content.

## BDD-13: Large spreadsheet truncation notice
**Traces to:** AC-13  **Category:** UI
**Given** an xlsx/csv exceeding the 500-row render cap
**When** the table renders
**Then** only the capped rows render and a visible truncation notice is shown
### Testing Flow
1. Upload (real upload flow) a CSV with >500 rows. 2. Open it and verify the cap + notice.

## BDD-14: Preview state survives back/forward navigation
**Traces to:** AC-14  **Category:** NAV
**Given** a user who previewed a file
**When** they navigate back to `/files` and forward again with browser buttons
**Then** the list view mode/scroll persists and the preview re-renders correctly
### Testing Flow
1. Preview a file. 2. Browser Back to the list, Forward to the file. 3. Verify preview renders again.

## BDD-15: Corrupt/unparseable office file shows error state
**Traces to:** AC-15  **Category:** ERR
**Given** a file with a spreadsheet/docx mime type whose bytes fail to parse
**When** the renderer throws
**Then** a "Could not load preview" error state is shown and the page remains usable
### Testing Flow
1. Upload (real upload flow) a text file renamed to `.xlsx` (wrong bytes). 2. Open it and verify the error state, page still navigable.

## BDD-16: Missing/failed presigned URL shows error state
**Traces to:** AC-16  **Category:** ERR
**Given** the download-url request fails or returns no URL
**When** the detail page loads
**Then** the renderer shows its "No download URL available" / error state without crashing
### Testing Flow
1. Simulate URL failure (e.g. offline route or expired token edge). 2. Verify graceful error state. (Also covered by unit tests on renderer null-URL props.)

## BDD-17: Preview requires authentication
**Traces to:** AC-17  **Category:** RBAC
**Given** an unauthenticated visitor
**When** they open `/files/{id}`
**Then** they are redirected to the login page (gateway JWT middleware returns 401 to API calls)
### Testing Flow
1. Log out / clear tokens. 2. Navigate to a file detail URL. 3. Verify redirect to login.

## BDD-18: Large file does not freeze the page
**Traces to:** AC-18  **Category:** PERF
**Given** a large previewable file
**When** the preview loads
**Then** fetch/parse is async, existing truncation limits are respected, and the UI stays responsive
### Testing Flow
1. Open the largest seeded xlsx/pdf. 2. Verify the page remains interactive (sidebar, buttons) while/after loading.

---

## AC → BDD Traceability Matrix

| AC-ID | Category | AC Title | BDD Scenario(s) | Status |
|-------|----------|----------|-----------------|--------|
| AC-01 | FUNC | Preview reachable from file list | BDD-01 | Mapped |
| AC-02 | FUNC | Image renders inline | BDD-02 | Mapped |
| AC-03 | FUNC | PDF renders inline (incl. seeded) | BDD-03 | Mapped |
| AC-04 | FUNC | Text/code renders with line numbers | BDD-04 | Mapped |
| AC-05 | FUNC | Spreadsheet renders as table | BDD-05 | Mapped |
| AC-06 | FUNC | CSV renders as table | BDD-06 | Mapped |
| AC-07 | FUNC | Word doc renders inline | BDD-07 | Mapped |
| AC-08 | FUNC | Video plays inline | BDD-08 | Mapped |
| AC-09 | FUNC | Audio plays inline | BDD-09 | Mapped |
| AC-10 | FUNC | Graceful fallback for unsupported types | BDD-10 | Mapped |
| AC-11 | FUNC | Seed includes video/audio files | BDD-11 | Mapped |
| AC-12 | UI | Loading spinner while preview prepares | BDD-12 | Mapped |
| AC-13 | UI | Large spreadsheet truncation notice | BDD-13 | Mapped |
| AC-14 | NAV | Preview survives back/forward | BDD-14 | Mapped |
| AC-15 | ERR | Corrupt office file → error state | BDD-15 | Mapped |
| AC-16 | ERR | Missing/failed presigned URL → error state | BDD-16 | Mapped |
| AC-17 | RBAC | Preview requires authentication | BDD-17 | Mapped |
| AC-18 | PERF | Large files don't freeze the page | BDD-18 | Mapped |

### Coverage Summary
- Total AC: 18 · Total BDD: 18 · Categories: FUNC(11) UI(2) NAV(1) ERR(2) RBAC(1) PERF(1)
- Unmapped AC-IDs: NONE

## Executable specs
- Vitest unit: `frontend/client-app/src/components/files/file-preview.test.ts` (renderer selection + spreadsheet row-cap; AC-02..AC-10, AC-13)
- Cucumber BDD: `frontend/client-app/bdd/features/file-preview.feature` (AC-01, AC-17)
- Playwright e2e: `frontend/client-app/e2e/file-preview.spec.ts` (AC-01, AC-04, AC-06, AC-10, AC-13, AC-15, AC-16, AC-17 via real upload flow)
- Rust: `services/file-service` presign test asserting `response-content-type` in the presigned URL (AC-03)
- Seed: `testdata/generated/retail-drive/filegen.py` mp4/mp3 builders (AC-11)

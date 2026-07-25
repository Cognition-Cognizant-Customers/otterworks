# OTD-12 — File Preview for All File Types: BDD Requirements

Derived from the approved Stage-1 technical spec (AC-01…AC-18). The preview lives in the
file-detail page Preview panel (`/files/:id`); previews render real bytes fetched via the
presigned URL from `GET /api/v1/files/:id/download`, typed from the DynamoDB `mime_type`.

## BDD-01: Preview opens from the file list without a download
**Traces to:** AC-01   **Category:** FUNC
**Given** I am logged in and on `/files` with a seeded file visible
**When** I click the file
**Then** the file-detail page opens and the Preview panel renders content inline, with no file saved to disk
### Testing Flow
1. Log in, navigate to `/files`, click a file card. 2. Verify the Preview panel renders content and no browser download occurred.

## BDD-02: Image preview renders inline
**Traces to:** AC-02   **Category:** FUNC
**Given** a seeded `image/png` or `image/jpeg` file
**When** its detail page loads
**Then** the image renders inline from the presigned URL
### Testing Flow
1. Open a PNG's detail page. 2. Verify an `<img>` is visible with rendered pixels.

## BDD-03: PDF renders via blob-typed bytes
**Traces to:** AC-03   **Category:** FUNC
**Given** a seeded `application/pdf` file whose S3 object is served as `binary/octet-stream`
**When** its detail page loads
**Then** the bytes are fetched, typed `application/pdf`, and rendered in the embedded viewer with no download prompt
### Testing Flow
1. Open a PDF's detail page. 2. Verify the embedded viewer shows the document (blob: URL) and no download prompt appears.

## BDD-04: Text/code preview preserved
**Traces to:** AC-04   **Category:** FUNC
**Given** a seeded `text/markdown` or `application/json` file
**When** its detail page loads
**Then** the line-numbered text viewer renders the content
### Testing Flow
1. Open a markdown file's detail page. 2. Verify line numbers and text content are visible.

## BDD-05: CSV renders as a clean table
**Traces to:** AC-05   **Category:** FUNC
**Given** a seeded `text/csv` file
**When** its detail page loads
**Then** the CSV is parsed and rendered as a table with a header row (not raw text)
### Testing Flow
1. Open a CSV's detail page. 2. Verify a `<table>` with header cells matching the CSV's first row.

## BDD-06: XLSX renders as a table with sheet tabs
**Traces to:** AC-06   **Category:** FUNC
**Given** a seeded `.xlsx` file
**When** its detail page loads
**Then** the workbook is parsed (SheetJS) and the first sheet renders as a table with clickable sheet tabs
### Testing Flow
1. Open an xlsx detail page. 2. Verify the table renders with real cell values and a sheet tab strip.

## BDD-07: DOCX renders as a formatted document
**Traces to:** AC-07   **Category:** FUNC
**Given** a seeded `.docx` file
**When** its detail page loads
**Then** mammoth converts it to HTML and headings/paragraphs render inline
### Testing Flow
1. Open a docx detail page. 2. Verify formatted headings/paragraph text render.

## BDD-08: Audio plays via an audio player
**Traces to:** AC-08   **Category:** FUNC
**Given** an uploaded `audio/*` file (seeded short sample)
**When** its detail page loads
**Then** an `<audio controls>` player renders with the presigned source
### Testing Flow
1. Open the seeded audio file's detail page. 2. Verify the audio player is present and playable.

## BDD-09: Video plays via a video player
**Traces to:** AC-09   **Category:** FUNC
**Given** an uploaded `video/*` file (seeded short sample)
**When** its detail page loads
**Then** a `<video controls>` player renders with the presigned source
### Testing Flow
1. Open the seeded video file's detail page. 2. Verify the video player is present and playable.

## BDD-10: Graceful fallback for unsupported types
**Traces to:** AC-10   **Category:** FUNC
**Given** a seeded `.pptx` file (unsupported for inline render)
**When** its detail page loads
**Then** a fallback card shows the file icon, name, type, size, and a Download button — no crash
### Testing Flow
1. Open a pptx detail page. 2. Verify the fallback card with name/type/size and Download button.

## BDD-11: Loading state while preview loads
**Traces to:** AC-11   **Category:** UI
**Given** any previewable file
**When** the presigned URL / bytes are still loading
**Then** a spinner is shown in the Preview panel (no premature error state)
### Testing Flow
1. Open a file detail page. 2. Observe the spinner before the preview content appears.

## BDD-12: Truncation notices for capped content
**Traces to:** AC-12, AC-18   **Category:** UI/PERF
**Given** a text file > 500 KB or a sheet with more rows than the row cap
**When** the preview renders
**Then** a truncation notice is shown and the page stays responsive
### Testing Flow
1. Unit-verify the row-cap logic (Vitest). 2. Browser-verify a capped table shows the truncation notice (using an uploaded large CSV).

## BDD-13: Correct preview across back/forward navigation
**Traces to:** AC-13   **Category:** NAV
**Given** I previewed file A, navigated back to `/files`, then opened file B
**When** I use browser back/forward
**Then** each detail page shows the correct file's preview with no stale content
### Testing Flow
1. Open file A, back to `/files`, open file B, browser-back, browser-forward. 2. Verify names/preview content match the current file each time.

## BDD-14: Corrupt office file falls back gracefully
**Traces to:** AC-14   **Category:** ERR
**Given** an xlsx/docx whose bytes fail to parse
**When** the preview attempts to parse
**Then** the fallback card renders with a "could not render preview" note and Download button
### Testing Flow
1. Unit-verify parse failure returns the error path (Vitest). 2. Browser-verify with an uploaded corrupt `.xlsx` (wrong bytes, xlsx mime).

## BDD-15: Fetch failure shows error with retry
**Traces to:** AC-15   **Category:** ERR
**Given** fetching the file bytes returns a non-2xx response (e.g. expired signature)
**When** the preview loads
**Then** an error state with a Retry action is shown (no infinite spinner)
### Testing Flow
1. Unit-verify the fetch-error path (Vitest). 2. Browser-verify the retry control renders when the byte fetch fails.

## BDD-16: Missing download URL handled
**Traces to:** AC-16   **Category:** ERR
**Given** `GET /api/v1/files/:id/download` errors so no URL is available
**When** the detail page renders
**Then** a "No download URL available" style message shows in the panel
### Testing Flow
1. Unit-verify components render the no-URL message when given no URL (Vitest).

## BDD-17: Preview follows existing auth rules
**Traces to:** AC-17   **Category:** RBAC
**Given** no JWT is presented
**When** `GET /api/v1/files/:id/download` is requested
**Then** the gateway rejects with 401 (preview only reachable when logged in)
### Testing Flow
1. `curl` the download endpoint without a token → 401. 2. Browser-verify `/files/:id` redirects to login when logged out.

## Data Dependencies
- **Stores:** DynamoDB `otterworks-file-metadata` (`id`, `name`, `mime_type`, `size_bytes`, `s3_key`); S3 `otterworks-files` (bytes via presigned URL).
- **Endpoints:** `GET /api/v1/files/:id` (metadata), `GET /api/v1/files/:id/download` → `{url, expiresInSecs}` (file-service, Rust/Actix, unchanged).
- **Component → service → endpoint:** `file-detail.tsx` → `filesApi.get`/`filesApi.getDownloadUrl` (`src/lib/api.ts`) → API gateway :8080 → file-service :8082 → DynamoDB/S3 (LocalStack locally).
- **Frontend units:** `src/lib/preview.ts` (pure helpers: preview-kind dispatch, CSV parse, row cap), `src/components/files/file-preview.tsx` (renderers), `src/pages/file-detail.tsx` (dispatch).

## AC → BDD Traceability Matrix
| AC-ID | Category | AC Title | BDD Scenario(s) | Status |
|-------|----------|----------|-----------------|--------|
| AC-01 | FUNC | Preview opens from file list without download | BDD-01 | Mapped |
| AC-02 | FUNC | Image preview | BDD-02 | Mapped |
| AC-03 | FUNC | PDF renders via blob-typed bytes | BDD-03 | Mapped |
| AC-04 | FUNC | Text/code preview | BDD-04 | Mapped |
| AC-05 | FUNC | CSV renders as a clean table | BDD-05 | Mapped |
| AC-06 | FUNC | XLSX table with sheet tabs | BDD-06 | Mapped |
| AC-07 | FUNC | DOCX formatted document | BDD-07 | Mapped |
| AC-08 | FUNC | Audio preview | BDD-08 | Mapped |
| AC-09 | FUNC | Video preview | BDD-09 | Mapped |
| AC-10 | FUNC | Graceful fallback for unsupported types | BDD-10 | Mapped |
| AC-11 | UI | Loading state | BDD-11 | Mapped |
| AC-12 | UI | Truncation notices | BDD-12 | Mapped |
| AC-13 | NAV | Preview across back/forward navigation | BDD-13 | Mapped |
| AC-14 | ERR | Corrupt office file fallback | BDD-14 | Mapped |
| AC-15 | ERR | Fetch failure → error with retry | BDD-15 | Mapped |
| AC-16 | ERR | Missing download URL message | BDD-16 | Mapped |
| AC-17 | RBAC | Unauthenticated request rejected | BDD-17 | Mapped |
| AC-18 | PERF | Row caps keep tables responsive | BDD-12 | Mapped |

### Coverage Summary
- Total AC: 18 · Total BDD: 17 · Categories: FUNC(10) UI(2) NAV(1) ERR(3) RBAC(1) PERF(1)
- Unmapped AC-IDs: NONE

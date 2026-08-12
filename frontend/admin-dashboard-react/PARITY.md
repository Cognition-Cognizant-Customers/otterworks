# Parity Checklist — Angular → React migration (Incidents + Quotas)

Contract observed in the Angular app (`frontend/admin-dashboard`) **before** writing any
React code. Each line is checked off once the React implementation
(`frontend/admin-dashboard-react`) reproduces it.

## Shared contract

- [x] API base URL is `/api/v1` (same-origin; dev server proxies to admin-service via the gateway)
- [x] Auth: `Authorization: Bearer <token>` header on every request; token read from `localStorage["ow_admin_token"]`
- [x] Auth is mocked (any email + any non-empty password); login stores token + user (`ow_admin_user`) in localStorage
- [x] 401 response logs the user out and redirects to `/login`
- [x] Route paths preserved: `/incidents`, `/quotas`, `/login` (guarded shell redirects unauthenticated users to `/login`)

## Incidents page (`/incidents`)

### Endpoints called (unchanged — no admin-service change)

- [x] `GET /api/v1/admin/incidents` → `{ incidents: [...] }`; each incident is snake_case: `id, title, description, severity(low|medium|high|critical), status(open|investigating|resolved|closed), affected_service, devin_session_id, devin_session_url, devin_session_status, reporter_id, resolved_at, closed_at, active, created_at, updated_at`
- [x] `POST /api/v1/admin/incidents` with body `{ incident: { title, description, severity, affected_service } }` → `{ incident: {...} }` (or bare incident)
- [x] `PATCH /api/v1/admin/incidents/:id` with body `{ incident: { status } }` (used for `resolved` and `closed`)
- [x] `DELETE /api/v1/admin/incidents/:id`
- [x] `POST /api/v1/admin/incidents/:id/trigger_session` with empty `{}` body → updated incident
- [x] `GET /api/v1/admin/settings/auto_investigate` → `{ enabled: boolean }`
- [x] `PUT /api/v1/admin/settings/auto_investigate` with body `{ enabled }` → `{ enabled: boolean }`
- [x] `POST /api/v1/admin/chaos` with body `{ service, scenario }` → `{ status, key, expires_in }`
- [x] `DELETE /api/v1/admin/chaos` → `{ status, cleared: string[], resolved_incidents?: string[] }`

### Loading / empty / error states

- [x] Spinner while the incident list request is in flight
- [x] Empty-state copy does NOT render while the first request is in flight
- [x] First-run onboarding card ("Automated Incident Response", 3 steps, "Report Your First Incident" CTA) when there are zero incidents and no filter
- [x] Filtered-empty state ("No {status} incidents" + "Clear Filter" button) when a filter matches nothing
- [x] List load failure → "Failed to load incidents" toast/snackbar (spinner cleared)
- [x] Create failure → "Failed to create incident" toast; success → "Incident created!" + session suffix (" Devin session launched." / " (Devin session pending)")
- [x] Resolve/close failure → toast with server `error.details` fallback to generic message
- [x] Delete failure → toast with `error.details` / `error.error` fallback to generic message
- [x] Trigger-session failure → "Failed to launch Devin session" toast and status reset
- [x] Auto-investigate GET failure → defaults to enabled; PUT failure → toast + toggle reverts to previous value
- [x] Chaos trigger failure → "Failed to trigger chaos on {service}" toast; reset failure → "Failed to reset chaos flags" toast

### Display & formatting

- [x] Header: "Incident Response" title + subtitle; "Report Incident"/"Cancel" toggle button
- [x] Status summary chips (only when incidents exist and not loading): "{n} Active", "{n} Devin Investigating", "{n} Resolved", "{n} Closed (hidden)" — chips toggle the status filter; closed chip toggles showing closed incidents
- [x] Counts: Active = `active` flag; Investigating = status `investigating` AND has devinSessionId; Resolved / Closed by status
- [x] Closed incidents hidden by default from the list
- [x] Incident card: left border + chip colored by severity; status chip with icon; service chip when `affected_service` present; description; created/resolved/closed timestamps formatted like Angular `date:'medium'` (e.g. "Aug 12, 2026, 7:00:00 AM")
- [x] Devin session block: session id (monospace), status badge, "View Session" link (new tab) when `devin_session_url` present
- [x] "No Devin session" block with "Launch Devin" button (disabled + "Launching..." while triggering) for active incidents without a session

### User actions

- [x] Report Incident form: labeled "Incident Title" input, labeled "Description" textarea, labeled "Severity" select (Low/Medium/High/Critical, default High), labeled "Affected Service" select (11 services with language suffix); submit disabled until title+description present; button shows "Creating Devin Session..." while creating
- [x] Resolve (only for open/investigating) with confirm dialog "Mark "{title}" as resolved?"
- [x] Close (only for resolved) with confirm dialog "Close "{title}"? Closed incidents are hidden from the default view."
- [x] Delete (always) with confirm dialog "Permanently delete "{title}"? This cannot be undone."
- [x] Demo Controls panel (collapsible, open by default): 4 chaos scenarios (search-service/suggest_500, file-service/upload_s3_error, notification-service/consumer_strict_schema, document-service/slow_queries) with per-scenario active state, "N active" badge, "Reset All" button, chaos state persisted in `localStorage["ow_admin_chaos_state"]`
- [x] Auto-Investigate toggle with ON/OFF descriptions, disabled while saving
- [x] Poll incident list every 10s while any active incident has a Devin session

## Quotas page (`/quotas`)

### Endpoints called (unchanged)

- [x] `GET /api/v1/admin/users` → `{ users: [...] }`; user fields used: `id, email, display_name, storage_quota: { used_bytes, quota_bytes }` (quota defaults to 5 GiB when missing)
- [x] `PUT /api/v1/admin/quotas/:userId` with body `{ quota: { quota_bytes } }` → `{ used_bytes, quota_bytes }`

### Loading / empty / error states

- [x] Spinner while the users request is in flight; table only renders after load
- [x] Empty-state copy does NOT render while the first request is in flight
- [x] Users load failure → error toast (React improvement: Angular silently spun forever; React clears the spinner and shows an error state — flagged as deliberate)
- [x] Quota update success → toast "Quota updated for {displayName}"; failure → error toast (Angular had no error path; React adds one per migration requirement)

### Display & formatting

- [x] Title "Storage Quotas"
- [x] Labeled search field ("Search users", placeholder "Search by name or email") filtering by any field (name/email), case-insensitive
- [x] Table columns: User (avatar icon + name + email), Used, Quota, Usage, Update Quota
- [x] Used/Quota formatted via 1024-based `formatBytes` with one decimal (e.g. "1.5 GB", "0 B")
- [x] Usage column: determinate progress bar + integer percent label; bar turns warn color above 90%
- [x] Sortable columns: User, Used, Quota
- [x] Pagination with page-size options 5 / 10 / 25 and first/last buttons

### User actions

- [x] Per-row quota select with options 1 / 2 / 5 / 10 / 20 / 50 GB (values in bytes), current quota preselected; change fires the PUT immediately

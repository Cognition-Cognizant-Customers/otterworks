# OtterWorks API Route Matrix

This matrix captures the primary API endpoints used to design and expand the black-box API flow tests in `tests/api`.

## Gateway prefixes

| Prefix | Service | Notes |
| --- | --- | --- |
| `/api/v1/auth` | Auth service | Registration, login, profile, refresh, logout, admin user listing. |
| `/api/v1/files` | File service | File upload, metadata, download, versions, trash/restore, share. |
| `/api/v1/documents` | Document service | Document CRUD, versions, export, comments, template instantiation. |
| `/api/v1/collab` | Collaboration service | Presence APIs and Socket.IO collaboration. |
| `/api/v1/notifications` | Notification service | Notification listing and lifecycle. |
| `/api/v1/search` | Search service | Search, suggest, advanced search, indexing. |
| `/api/v1/analytics` | Analytics service | Event ingestion and aggregate metrics. |
| `/api/v1/admin` | Admin service | User, quota, feature, announcement, audit-log, metrics, health admin APIs. |
| `/api/v1/audit` | Audit service | Audit events, history, reports, export, archive. |

## Critical user-flow routes

| Flow | Routes | Current API test coverage |
| --- | --- | --- |
| Account/session lifecycle | `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `GET /api/v1/auth/profile`, `PUT /api/v1/auth/profile`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/users` | `tests/api/test_auth_flow.py` |
| Document authoring | `POST /api/v1/documents/`, `GET /api/v1/documents/`, `GET /api/v1/documents/{id}`, `PUT /api/v1/documents/{id}`, `PATCH /api/v1/documents/{id}`, `DELETE /api/v1/documents/{id}` | `tests/api/test_document_flow.py` |
| Document history/export | `GET /api/v1/documents/{id}/versions`, `POST /api/v1/documents/{id}/versions/{version_id}/restore`, `GET /api/v1/documents/{id}/export` | `tests/api/test_document_flow.py` |
| Comments | `POST /api/v1/documents/{id}/comments`, `GET /api/v1/documents/{id}/comments`, `DELETE /api/v1/documents/{id}/comments/{comment_id}` | `tests/api/test_document_flow.py` |
| Templates | `GET /api/v1/templates`, `POST /api/v1/templates`, `POST /api/v1/documents/from-template/{template_id}` | Partial direct-service gap: gateway currently has no `/api/v1/templates` prefix in `ServiceRoutes`. |
| Share declination rules | `POST /api/v1/share-requests` | `services/document-service/tests/test_share_requests_api.py`; direct-service only — gateway has no `/api/v1/share-requests` prefix in `ServiceRoutes`. |
| File lifecycle | `POST /api/v1/files/upload`, `GET /api/v1/files`, `GET /api/v1/files/{id}`, `GET /api/v1/files/{id}/download`, `PUT /api/v1/files/{id}/move`, `POST /api/v1/files/{id}/trash`, `POST /api/v1/files/{id}/restore`, `DELETE /api/v1/files/{id}` | Planned next API flow suite. |
| Folder lifecycle | `POST /api/v1/folders`, `GET /api/v1/folders/{id}`, `PUT /api/v1/folders/{id}`, `DELETE /api/v1/folders/{id}` | Gateway prefix gap: `/api/v1/folders` is not currently in `ServiceRoutes`. |
| Search/discovery | `GET /api/v1/search`, `GET /api/v1/search/suggest`, `POST /api/v1/search/advanced`, `POST /api/v1/search/index/document`, `POST /api/v1/search/index/file`, `DELETE /api/v1/search/index/{type}/{id}`, `POST /api/v1/search/reindex` | Planned next API flow suite. |
| Collaboration | `GET /api/v1/collab/documents`, `GET /api/v1/collab/documents/{id}/presence`, Socket.IO connection to collab service | Planned WebSocket/API suite. |
| Notifications/preferences | `GET /api/v1/notifications`, notification lifecycle routes, `/api/v1/preferences` | Gateway prefix gap: preferences are not currently in `ServiceRoutes`. |
| Audit | `POST /api/v1/audit/events`, `GET /api/v1/audit/events`, `GET /api/v1/audit/events/{id}`, `GET /api/v1/audit/resources/{resourceId}/history`, `GET /api/v1/audit/export`, `POST /api/v1/audit/archive` | Planned side-effect and direct API suite. |
| Reports | `POST /api/v1/reports`, `GET /api/v1/reports`, `GET /api/v1/reports/{id}`, `GET /api/v1/reports/{id}/download`, `DELETE /api/v1/reports/{id}` | Gateway prefix gap: report service is configured in Compose but not in `ServiceRoutes`. |

## Known route and behavior gaps to verify

- **Templates**: Document service exposes `/api/v1/templates`, but the gateway currently only routes `/api/v1/documents` to the document service.
- **Folders**: File service exposes `/api/v1/folders`, but the gateway currently only routes `/api/v1/files` to the file service.
- **Reports**: Compose config includes `REPORT_SERVICE_URL`, but gateway config does not currently route `/api/v1/reports`.
- **Preferences**: Notification service exposes `/api/v1/preferences`, but gateway config does not currently route that prefix.
- **Document ownership**: Document service endpoints should be tested for cross-user access; the current black-box suite expects denial for cross-user reads.
- **JWT identity propagation**: Gateway forwards `X-User-ID` from JWT claims, while some services also decode JWTs directly; tests should catch claim/header mismatches.

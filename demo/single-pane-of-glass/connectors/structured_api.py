"""System 1 — the structured "mainframe / DB2" analog.

Pulls records from the OtterWorks enterprise drive through the public API
gateway: the 15 department folders, every file's size/type, rich-text
documents, and the recent activity feed. This is the structured, queryable
system in the single-pane-of-glass picture.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

TIMEOUT = 30


def _assert_http(url):
    """Only allow http(s) — blocks file://, ftp:// etc. before any fetch."""
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"refusing non-http(s) URL scheme: {scheme!r}")


def _request(method, url, token=None, body=None):
    _assert_http(url)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    # URL scheme is allowlisted above (http/https only); host is trusted config.
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def login(gateway, email, password):
    if not email or not password:
        raise RuntimeError(
            "DRIVE_EMAIL / DRIVE_PASSWORD not set — provide them from the vault."
        )
    resp = _request(
        "POST",
        f"{gateway}/api/v1/auth/login",
        body={"email": email, "password": password},
    )
    return resp["accessToken"]


def _get(gateway, token, path, params=None):
    url = f"{gateway}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _request("GET", url, token=token)


def _list_folders(gateway, token, parent_id=None):
    params = {"parent_id": parent_id} if parent_id else None
    return _get(gateway, token, "/api/v1/folders", params).get("folders", [])


def _build_folder_tree(gateway, token):
    """Return (folder_id -> root_department_name) for the whole tree."""
    roots = _list_folders(gateway, token)
    departments = [f["name"] for f in roots]
    folder_root = {}
    queue = [(f["id"], f["name"]) for f in roots]
    for fid, root_name in queue:
        folder_root[fid] = root_name
    i = 0
    while i < len(queue):
        fid, root_name = queue[i]
        i += 1
        for child in _list_folders(gateway, token, fid):
            if child["id"] not in folder_root:
                folder_root[child["id"]] = root_name
                queue.append((child["id"], root_name))
    return departments, folder_root


def _all_files(gateway, token):
    files, page, page_size = [], 1, 100
    while True:
        resp = _get(
            gateway, token, "/api/v1/files",
            {"page": page, "page_size": page_size},
        )
        batch = resp.get("files", [])
        files.extend(batch)
        total = resp.get("total", 0)
        if len(files) >= total or not batch:
            break
        page += 1
    return files


def _all_documents(gateway, token):
    docs, page, size = [], 1, 100
    while True:
        resp = _get(
            gateway, token, "/api/v1/documents",
            {"page": page, "pageSize": size},
        )
        batch = resp.get("items", [])
        docs.extend(batch)
        if len(docs) >= resp.get("total", 0) or not batch:
            break
        page += 1
    return docs


_MIME_LABELS = {
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "Spreadsheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word doc",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "Presentation",
    "text/csv": "CSV",
    "text/plain": "Text",
    "image/png": "Image",
    "image/jpeg": "Image",
    "application/json": "JSON",
}


def _friendly_mime(mime):
    if mime in _MIME_LABELS:
        return _MIME_LABELS[mime]
    if mime.startswith("image/"):
        return "Image"
    if mime.startswith("text/"):
        return "Text"
    return mime.split("/")[-1][:14] or "Other"


def collect(gateway=None, email=None, password=None):
    gateway = gateway or config.GATEWAY_URL
    email = email or config.DRIVE_EMAIL
    password = password or config.DRIVE_PASSWORD

    token = login(gateway, email, password)
    departments, folder_root = _build_folder_tree(gateway, token)
    files = _all_files(gateway, token)
    documents = _all_documents(gateway, token)

    dept_files = defaultdict(int)
    dept_bytes = defaultdict(int)
    mime_counts = defaultdict(int)
    total_bytes = 0
    for f in files:
        dept = folder_root.get(f.get("folder_id"), "Unfiled")
        size = f.get("size_bytes", 0) or 0
        dept_files[dept] += 1
        dept_bytes[dept] += size
        total_bytes += size
        mime_counts[_friendly_mime(f.get("mime_type", ""))] += 1

    dept_docs = defaultdict(int)
    for d in documents:
        title = d.get("title", "")
        # Seeded titles are "Department \u2014 <name>"
        dept = title.split("\u2014")[0].strip() if "\u2014" in title else "General"
        dept_docs[dept] += 1

    dept_rows = []
    for name in departments:
        dept_rows.append({
            "name": name,
            "file_count": dept_files.get(name, 0),
            "storage_bytes": dept_bytes.get(name, 0),
            "document_count": dept_docs.get(name, 0),
        })
    dept_rows.sort(key=lambda r: r["storage_bytes"], reverse=True)

    activity_raw = _get(
        gateway, token, "/api/v1/files/activity", {"limit": 12}
    ).get("items", [])
    activity = [{
        "type": a.get("type", ""),
        "description": a.get("description", ""),
        "actor": a.get("actor_name", ""),
        "resource": a.get("resource_name", ""),
        "created_at": a.get("created_at", ""),
    } for a in activity_raw]

    file_types = sorted(
        ({"label": k, "count": v} for k, v in mime_counts.items()),
        key=lambda x: x["count"], reverse=True,
    )[:6]

    return {
        "source": "OtterWorks Enterprise Drive (API / DB)",
        "type": "Structured system",
        "logged_in_as": email,
        "total_files": len(files),
        "total_documents": len(documents),
        "total_storage_bytes": total_bytes,
        "department_count": len(departments),
        "departments": dept_rows,
        "file_types": file_types,
        "activity": activity,
        # Used only to reconcile against what the web UI shows; not rendered.
        "file_names": [f.get("name", "") for f in files],
    }


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))

#!/usr/bin/env python3
"""Negative teardown scan: assert zero prefixed objects remain in the workspace.

Scans Unity Catalog catalogs, jobs, secret scopes, and the /Shared workspace
tree for anything carrying the demo prefix (default ow_tp). Exit 0 only when
nothing is found. Auth: DATABRICKS_HOST/TOKEN (or DATABRICKS_DEMO_*)."""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

PREFIX = sys.argv[1] if len(sys.argv) > 1 else "ow_tp"
HOST = (os.environ.get("DATABRICKS_HOST") or os.environ["DATABRICKS_DEMO_HOST"]).rstrip("/")
TOKEN = os.environ.get("DATABRICKS_TOKEN") or os.environ["DATABRICKS_DEMO_TOKEN"]


def get(path: str) -> dict:
    req = urllib.request.Request(HOST + path)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise


leftovers: list[str] = []

for c in get("/api/2.1/unity-catalog/catalogs").get("catalogs", []):
    if c["name"].startswith(PREFIX):
        leftovers.append(f"catalog:{c['name']}")

jobs_path = "/api/2.2/jobs/list?limit=100"
while jobs_path:
    out = get(jobs_path)
    for j in out.get("jobs", []):
        name = j.get("settings", {}).get("name", "")
        if name.startswith(PREFIX):
            leftovers.append(f"job:{name} (id={j['job_id']})")
    token = out.get("next_page_token")
    jobs_path = f"/api/2.2/jobs/list?limit=100&page_token={token}" if token else None

for s in get("/api/2.0/secrets/scopes/list").get("scopes", []):
    if s["name"].startswith(PREFIX):
        leftovers.append(f"secret-scope:{s['name']}")

shared = get("/api/2.0/workspace/list?path=" + urllib.parse.quote("/Shared"))
for obj in shared.get("objects", []):
    base = obj["path"].rsplit("/", 1)[-1]
    if base.startswith(PREFIX):
        leftovers.append(f"workspace:{obj['path']}")

if leftovers:
    print(f"TEARDOWN INCOMPLETE — {len(leftovers)} '{PREFIX}'-prefixed object(s) remain:")
    for item in leftovers:
        print(f"  {item}")
    sys.exit(1)

print(f"teardown verified: zero '{PREFIX}'-prefixed catalogs, jobs, secret scopes, or /Shared objects remain")

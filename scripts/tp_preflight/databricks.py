#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
import urllib.parse

from common import Manifest, require_env


require_env("DATABRICKS_DEMO_HOST", "DATABRICKS_DEMO_TOKEN")
HOST = os.environ["DATABRICKS_DEMO_HOST"].rstrip("/")
TOKEN = os.environ["DATABRICKS_DEMO_TOKEN"]
manifest = Manifest("databricks")


def call(method: str, path: str, body=None):
    req = urllib.request.Request(
        HOST + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        return exc.code, raw
    except Exception as exc:
        return 0, str(exc)


def probe(pid, description, api, action, cleanup=None):
    try:
        status, detail = action()
        if 200 <= status < 300:
            manifest.add(pid, description, api, "verified", f"HTTP {status}")
            return detail
        manifest.add(pid, description, api, "denied", f"HTTP {status}: {detail}")
    except Exception as exc:
        manifest.add(pid, description, api, "denied", str(exc))
    finally:
        if cleanup:
            cleanup()
    return None


identity = call("GET", "/api/2.0/preview/scim/v2/Me")
if 200 <= identity[0] < 300:
    manifest.data["credential_identity"] = identity[1].get("userName", "available")
else:
    manifest.data["credential_identity"] = "unavailable"
    manifest.add("authenticate", "PAT can identify the caller", "/api/2.0/current-user",
                 "denied", f"HTTP {identity[0]}: {identity[1]}")

landing = "/Volumes/ow_tp/bronze/landing"
suffix = f"__tp_preflight_{uuid.uuid4().hex}"
file_path = f"{landing}/{suffix}"
payload = b"otterworks tp preflight\n"
landing_api = "/api/2.0/fs/directories" + urllib.parse.quote(landing, safe="/")
file_api = "/api/2.0/fs/files" + urllib.parse.quote(file_path, safe="/")
req = urllib.request.Request(HOST + landing_api, headers={"Authorization": f"Bearer {TOKEN}"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        manifest.add("files-get-directory", "List the landing volume directory", "Files API GET", "verified", f"HTTP {r.status}")
except Exception as exc:
    manifest.add("files-get-directory", "List the landing volume directory", "Files API GET", "denied", str(exc))
try:
    req = urllib.request.Request(HOST + file_api, data=payload, headers={"Authorization": f"Bearer {TOKEN}"}, method="PUT")
    with urllib.request.urlopen(req, timeout=30) as r:
        manifest.add("files-put", "Write a temporary landing file", "Files API PUT", "verified", f"HTTP {r.status}")
    req = urllib.request.Request(HOST + file_api, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        got = r.read()
        result = "verified" if got == payload else "denied"
        manifest.add("files-get-file", "Read the temporary landing file", "Files API GET", result, f"HTTP {r.status}, {len(got)} bytes")
except Exception as exc:
    manifest.add("files-put-get", "Write and read a temporary landing file", "Files API PUT/GET", "denied", str(exc))
finally:
    req = urllib.request.Request(HOST + file_api, headers={"Authorization": f"Bearer {TOKEN}"}, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE", "verified", f"HTTP {r.status}")
    except Exception as exc:
        manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE", "denied", str(exc))

warehouse_id = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "")
if not warehouse_id:
    warehouse_probe = call("GET", "/api/2.0/sql/warehouses")
    if 200 <= warehouse_probe[0] < 300:
        for warehouse in warehouse_probe[1].get("warehouses", []):
            if warehouse.get("enable_serverless_compute") or warehouse.get("warehouse_type") == "PRO":
                warehouse_id = warehouse.get("id", "")
                break
schema = f"ow_tp_preflight_{uuid.uuid4().hex[:12]}"
created_schema = call("POST", "/api/2.0/sql/statements", {"statement": f"CREATE SCHEMA ow_tp.{schema}", "warehouse_id": warehouse_id})
if 200 <= created_schema[0] < 300:
    listed_schema = call("GET", "/api/2.1/unity-catalog/schemas?catalog_name=ow_tp")
    manifest.add("uc-create-list", "Create and list a temporary Unity Catalog schema", "SQL Statement + Unity Catalog APIs", "verified" if 200 <= listed_schema[0] < 300 else "denied", f"create HTTP {created_schema[0]}, list HTTP {listed_schema[0]}")
else:
    manifest.add("uc-create-list", "Create and list a temporary Unity Catalog schema", "SQL Statement + Unity Catalog APIs", "denied", f"HTTP {created_schema[0]}: {created_schema[1]}")
call("POST", "/api/2.0/sql/statements", {"statement": f"DROP SCHEMA IF EXISTS ow_tp.{schema} CASCADE", "warehouse_id": warehouse_id})

job_name = f"ow_tp_preflight_{uuid.uuid4().hex[:8]}"
job = call("POST", "/api/2.1/jobs/create", {"name": job_name, "tasks": [{"task_key": "noop", "notebook_task": {"notebook_path": "/Shared/ow_tp/preflight"}}]})
if 200 <= job[0] < 300:
    listed_jobs = call("GET", "/api/2.1/jobs/list?name=" + job_name)
    manifest.add("jobs-create-list", "Create and list a temporary job", "Jobs API 2.1", "verified" if 200 <= listed_jobs[0] < 300 else "denied", f"create HTTP {job[0]}, list HTTP {listed_jobs[0]}")
    call("POST", "/api/2.0/jobs/delete", {"job_id": job[1].get("job_id")})
else:
    manifest.add("jobs-create-list", "Create and list a temporary job", "Jobs API 2.1", "denied", f"HTTP {job[0]}: {job[1]}")

scope_name = f"ow_tp_preflight_{uuid.uuid4().hex[:8]}"
scope = call("POST", "/api/2.0/secrets/scopes/create", {"scope": scope_name})
if 200 <= scope[0] < 300:
    manifest.add("secret-scope", "Create and delete a temporary secret scope", "Secrets API 2.0", "verified", f"create HTTP {scope[0]}")
    call("POST", "/api/2.0/secrets/scopes/delete", {"scope": scope_name})
else:
    manifest.add("secret-scope", "Create and delete a temporary secret scope", "Secrets API 2.0", "denied", f"HTTP {scope[0]}: {scope[1]}")

warehouses = call("GET", "/api/2.0/sql/warehouses")
if 200 <= warehouses[0] < 300:
    serverless = [w for w in warehouses[1].get("warehouses", []) if w.get("enable_serverless_compute") or w.get("warehouse_type") == "PRO"]
    manifest.add("serverless-warehouse", "An existing serverless SQL warehouse is available", "SQL Warehouses API", "verified" if serverless else "denied", json.dumps(serverless[:3]) if serverless else "no serverless warehouse")
else:
    manifest.add("serverless-warehouse", "An existing serverless SQL warehouse is available", "SQL Warehouses API", "denied", f"HTTP {warehouses[0]}: {warehouses[1]}")

raise SystemExit(manifest.write("databricks"))

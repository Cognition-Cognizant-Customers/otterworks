#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
import urllib.parse

from common import Manifest, exception_detail, require_env


require_env("DATABRICKS_DEMO_HOST", "DATABRICKS_DEMO_TOKEN")
HOST = os.environ["DATABRICKS_DEMO_HOST"].rstrip("/")
TOKEN = os.environ["DATABRICKS_DEMO_TOKEN"]
catalog = os.environ.get("TP_DATABRICKS_CATALOG", "ow_tp")
landing = os.environ.get("TP_DATABRICKS_LANDING_PATH", f"/Volumes/{catalog}/bronze/landing")
if not re.fullmatch(r"[A-Za-z0-9_]+", catalog):
    raise SystemExit(f"TP_DATABRICKS_CATALOG must match [A-Za-z0-9_]+: {catalog!r}")
if not landing.startswith("/Volumes/") or ".." in landing.split("/"):
    raise SystemExit(
        "TP_DATABRICKS_LANDING_PATH must start with /Volumes/ and contain no '..' segments: "
        f"{landing!r}"
    )
landing_catalog = landing.split("/", 3)[2] if len(landing.split("/", 3)) > 2 else ""
if not re.fullmatch(r"[A-Za-z0-9_]+", landing_catalog) or landing_catalog != catalog:
    raise SystemExit(
        "TP_DATABRICKS_LANDING_PATH must use the configured catalog segment "
        f"{catalog!r}: {landing!r}"
    )
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
        return 0, exception_detail(exc)


def sql_call(statement: str, warehouse_id: str):
    initial_status, body = call(
        "POST",
        "/api/2.0/sql/statements",
        {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "30s",
            "on_wait_timeout": "CANCEL",
        },
    )
    status = initial_status
    if 200 <= initial_status < 300 and isinstance(body, dict):
        statement_id = body.get("statement_id")
        state = body.get("status", {}).get("state")
        for _ in range(30):
            if state in {"SUCCEEDED", "FAILED", "CANCELED"} or not statement_id:
                break
            time.sleep(1)
            status, body = call("GET", f"/api/2.0/sql/statements/{statement_id}")
            state = body.get("status", {}).get("state") if isinstance(body, dict) else None
    return status, body, 200 <= initial_status < 300


def sql_detail(result):
    status, body, _ = result
    if not isinstance(body, dict):
        return f"HTTP {status}"
    statement_status = body.get("status", {})
    state = statement_status.get("state", "unknown")
    error = statement_status.get("error", {})
    message = error.get("message") if isinstance(error, dict) else error
    return f"HTTP {status}, state={state}" + (f", error={message}" if message else "")


def response_detail(status, body):
    if isinstance(body, dict):
        message = (
            body.get("error_code")
            or body.get("errorCode")
            or body.get("message")
            or body.get("detail")
            or body.get("error")
        )
        return f"HTTP {status}" + (f": {message}" if message else "")
    return f"HTTP {status}"


def probe(pid, description, api, action, cleanup=None):
    try:
        status, detail = action()
        if 200 <= status < 300:
            manifest.add(pid, description, api, "verified", f"HTTP {status}")
            return detail
        manifest.add(pid, description, api, "denied", f"HTTP {status}: {detail}")
    except Exception as exc:
        manifest.add(pid, description, api, "denied", exception_detail(exc))
    finally:
        if cleanup:
            cleanup()
    return None


identity_status, identity_body = call("GET", "/api/2.0/preview/scim/v2/Me")
if 200 <= identity_status < 300:
    manifest.data["credential_identity"] = identity_body.get("userName", "available")
else:
    manifest.data["credential_identity"] = "unavailable"
    manifest.add("authenticate", "PAT can identify the caller", "/api/2.0/current-user",
                 "denied", f"HTTP {identity_status}")

suffix = f"__tp_preflight_{uuid.uuid4().hex}"
file_path = f"{landing}/{suffix}"
payload = b"otterworks tp preflight\n"
put_succeeded = False
landing_api = "/api/2.0/fs/directories" + urllib.parse.quote(landing, safe="/")
file_api = "/api/2.0/fs/files" + urllib.parse.quote(file_path, safe="/")
req = urllib.request.Request(HOST + landing_api, headers={"Authorization": f"Bearer {TOKEN}"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        manifest.add("files-get-directory", "List the landing volume directory", "Files API GET", "verified", f"HTTP {r.status}")
except Exception as exc:
    manifest.add("files-get-directory", "List the landing volume directory", "Files API GET", "denied", exception_detail(exc))
try:
    req = urllib.request.Request(HOST + file_api, data=payload, headers={"Authorization": f"Bearer {TOKEN}"}, method="PUT")
    with urllib.request.urlopen(req, timeout=30) as r:
        manifest.add("files-put", "Write a temporary landing file", "Files API PUT", "verified", f"HTTP {r.status}")
        put_succeeded = True
    req = urllib.request.Request(HOST + file_api, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        got = r.read()
        result = "verified" if got == payload else "denied"
        manifest.add("files-get-file", "Read the temporary landing file", "Files API GET", result, f"HTTP {r.status}, {len(got)} bytes")
except Exception as exc:
    manifest.add("files-put-get", "Write and read a temporary landing file", "Files API PUT/GET", "denied", exception_detail(exc))
finally:
    if put_succeeded:
        req = urllib.request.Request(HOST + file_api, headers={"Authorization": f"Bearer {TOKEN}"}, method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE", "verified", f"HTTP {r.status}")
        except Exception as exc:
            manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE", "denied", exception_detail(exc))
    else:
        manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE", "skipped", "PUT did not create a file")

warehouse_probe = call("GET", "/api/2.0/sql/warehouses")
warehouse_id = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "")
if not warehouse_id and 200 <= warehouse_probe[0] < 300:
    for warehouse in warehouse_probe[1].get("warehouses", []):
        if warehouse.get("enable_serverless_compute") or warehouse.get("warehouse_type") == "PRO":
            warehouse_id = warehouse.get("id", "")
            break
schema = f"ow_tp_preflight_{uuid.uuid4().hex[:12]}"
created_schema = sql_call(f"CREATE SCHEMA {catalog}.{schema}", warehouse_id)
create_accepted = created_schema[2]
create_succeeded = create_accepted and isinstance(created_schema[1], dict) and created_schema[1].get("status", {}).get("state") == "SUCCEEDED"
if create_succeeded:
    listed_schema = call("GET", f"/api/2.1/unity-catalog/schemas?catalog_name={urllib.parse.quote(catalog)}")
    manifest.add("uc-create-list", "Create and list a temporary Unity Catalog schema", "SQL Statement + Unity Catalog APIs", "verified" if 200 <= listed_schema[0] < 300 else "denied", f"{sql_detail(created_schema)}; list HTTP {listed_schema[0]}")
else:
    manifest.add("uc-create-list", "Create and list a temporary Unity Catalog schema", "SQL Statement + Unity Catalog APIs", "denied", sql_detail(created_schema))
if create_accepted:
    dropped_schema = sql_call(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE", warehouse_id)
    manifest.add("uc-schema-delete", "Delete the temporary Unity Catalog schema", "SQL Statement", "verified" if dropped_schema[0] >= 200 and dropped_schema[0] < 300 and dropped_schema[1].get("status", {}).get("state") == "SUCCEEDED" else "denied", sql_detail(dropped_schema))
else:
    manifest.add("uc-schema-delete", "Delete the temporary Unity Catalog schema", "SQL Statement", "skipped", "schema was not created")

job_name = f"ow_tp_preflight_{uuid.uuid4().hex[:8]}"
job = call("POST", "/api/2.1/jobs/create", {"name": job_name, "tasks": [{"task_key": "noop", "notebook_task": {"notebook_path": "/Shared/ow_tp/preflight"}}]})
if 200 <= job[0] < 300:
    listed_jobs = call("GET", "/api/2.1/jobs/list?name=" + job_name)
    manifest.add("jobs-create-list", "Create and list a temporary job", "Jobs API 2.1", "verified" if 200 <= listed_jobs[0] < 300 else "denied", f"create HTTP {job[0]}, list HTTP {listed_jobs[0]}")
    deleted_job = call("POST", "/api/2.0/jobs/delete", {"job_id": job[1].get("job_id")})
    manifest.add("jobs-delete", "Delete the temporary job", "Jobs API 2.0", "verified" if 200 <= deleted_job[0] < 300 else "denied", f"HTTP {deleted_job[0]}")
else:
    manifest.add("jobs-create-list", "Create and list a temporary job", "Jobs API 2.1", "denied", response_detail(job[0], job[1]))
    manifest.add("jobs-delete", "Delete the temporary job", "Jobs API 2.0", "skipped", "job was not created")

scope_name = f"ow_tp_preflight_{uuid.uuid4().hex[:8]}"
scope = call("POST", "/api/2.0/secrets/scopes/create", {"scope": scope_name})
if 200 <= scope[0] < 300:
    manifest.add("secret-scope", "Create and delete a temporary secret scope", "Secrets API 2.0", "verified", f"create HTTP {scope[0]}")
    deleted_scope = call("POST", "/api/2.0/secrets/scopes/delete", {"scope": scope_name})
    manifest.add("secret-scope-delete", "Delete the temporary secret scope", "Secrets API 2.0", "verified" if 200 <= deleted_scope[0] < 300 else "denied", f"HTTP {deleted_scope[0]}")
else:
    manifest.add("secret-scope", "Create and delete a temporary secret scope", "Secrets API 2.0", "denied", response_detail(scope[0], scope[1]))
    manifest.add("secret-scope-delete", "Delete the temporary secret scope", "Secrets API 2.0", "skipped", "scope was not created")

if 200 <= warehouse_probe[0] < 300:
    serverless = [w for w in warehouse_probe[1].get("warehouses", []) if w.get("enable_serverless_compute") or w.get("warehouse_type") == "PRO"]
    summary = [{"id": w.get("id"), "name": w.get("name"), "state": w.get("state")} for w in serverless[:3]]
    manifest.add("serverless-warehouse", "An existing serverless SQL warehouse is available", "SQL Warehouses API", "verified" if serverless else "denied", json.dumps(summary) if summary else "no serverless warehouse")
else:
    manifest.add("serverless-warehouse", "An existing serverless SQL warehouse is available", "SQL Warehouses API", "denied", f"HTTP {warehouse_probe[0]}")

raise SystemExit(manifest.write("databricks"))

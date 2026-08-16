#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid
import urllib.parse
from dataclasses import dataclass

from common import Manifest, exception_detail, require_env


require_env("DATABRICKS_DEMO_HOST", "DATABRICKS_DEMO_TOKEN")
raw_host = os.environ["DATABRICKS_DEMO_HOST"]
parsed_host = urllib.parse.urlparse(raw_host)
if parsed_host.scheme != "https" or not parsed_host.hostname or parsed_host.username or parsed_host.password:
    raise SystemExit("DATABRICKS_DEMO_HOST must be an HTTPS URL with a valid host")
valid_databricks_host = (
    parsed_host.hostname == "cloud.databricks.com"
    or parsed_host.hostname.endswith(".cloud.databricks.com")
    or parsed_host.hostname.endswith(".azuredatabricks.net")
    or parsed_host.hostname.endswith(".gcp.databricks.com")
)
if not valid_databricks_host:
    raise SystemExit("DATABRICKS_DEMO_HOST must use a Databricks workspace host")
HOST = raw_host.rstrip("/")
TOKEN = os.environ["DATABRICKS_DEMO_TOKEN"]
catalog = os.environ.get("TP_DATABRICKS_CATALOG", "ow_tp")
landing = os.environ.get("TP_DATABRICKS_LANDING_PATH", f"/Volumes/{catalog}/bronze/landing")
configured_warehouse_id = os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "")
if configured_warehouse_id and not re.fullmatch(r"[A-Za-z0-9]+", configured_warehouse_id):
    raise SystemExit(
        "DATABRICKS_SQL_WAREHOUSE_ID must match [A-Za-z0-9]+: "
        f"{configured_warehouse_id!r}"
    )
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


def handle_uncaught(exc_type, exc, traceback):
    try:
        manifest.add("internal-error", "Unhandled preflight failure", "preflight runtime",
                     "denied", exception_detail(exc))
        manifest.write("databricks")
    finally:
        sys.__excepthook__(exc_type, exc, traceback)


sys.excepthook = handle_uncaught


@dataclass(frozen=True)
class SqlResult:
    status: int
    body: dict
    accepted: bool
    state: str


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
            if not raw:
                return response.status, {}
            parsed = json.loads(raw)
            return response.status, parsed if isinstance(parsed, dict) else {"_raw_error": str(parsed)[:300]}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            parsed = json.loads(raw)
            return exc.code, parsed if isinstance(parsed, dict) else {"_raw_error": str(parsed)[:300]}
        except json.JSONDecodeError:
            return exc.code, {"_raw_error": raw[:300]}
    except Exception as exc:
        return 0, {"_raw_error": exception_detail(exc)}


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
    accepted = 200 <= initial_status < 300
    body = body if isinstance(body, dict) else {"_raw_error": str(body)[:300]}
    status = initial_status
    state = body.get("status", {}).get("state") if isinstance(body.get("status"), dict) else "unknown"
    if accepted:
        statement_id = body.get("statement_id")
        for _ in range(30):
            if state in {"SUCCEEDED", "FAILED", "CANCELED"} or not statement_id:
                break
            time.sleep(1)
            quoted_statement_id = urllib.parse.quote(str(statement_id), safe="")
            status, polled_body = call("GET", f"/api/2.0/sql/statements/{quoted_statement_id}")
            body = polled_body if isinstance(polled_body, dict) else {"_raw_error": str(polled_body)[:300]}
            state = body.get("status", {}).get("state") if isinstance(body.get("status"), dict) else "unknown"
    return SqlResult(status, body, accepted, state)


def sql_detail(result):
    status, body = result.status, result.body
    statement_status = body.get("status", {})
    state = statement_status.get("state", "unknown")
    error = statement_status.get("error", {})
    message = error.get("message") if isinstance(error, dict) else error
    raw_error = body.get("_raw_error")
    return f"HTTP {status}, state={state}" + (f", error={message or raw_error}" if message or raw_error else "")


def response_detail(status, body):
    if isinstance(body, dict):
        raw_error = body.get("_raw_error")
        message = (
            body.get("message")
            or body.get("detail")
            or body.get("error")
            or body.get("error_code")
            or body.get("errorCode")
        )
        return f"HTTP {status}" + (f": {message or raw_error}" if message or raw_error else "")
    return f"HTTP {status}"


def http_error_detail(error):
    try:
        raw = error.read().decode(errors="replace")
    except Exception:
        raw = ""
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {"_raw_error": raw[:300]}
    return response_detail(error.code, body)


cleanup_registry = {}


def register_cleanup(name, callback):
    cleanup_registry[name] = callback


def cleanup_all():
    for name, callback in list(cleanup_registry.items()):
        try:
            callback()
        except Exception as exc:
            manifest.add(f"{name}-cleanup", f"Cleanup temporary {name}", "Databricks cleanup",
                         "denied", exception_detail(exc))
        cleanup_registry.pop(name, None)


def handle_signal(signum, _frame):
    cleanup_all()
    manifest.write("databricks")
    raise SystemExit(128 + signum)


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


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
if 200 <= identity_status < 300 and isinstance(identity_body, dict):
    manifest.set_identity(identity_body.get("userName", "available"))
else:
    manifest.set_identity("unavailable")
    manifest.add("authenticate", "PAT can identify the caller", "GET /api/2.0/preview/scim/v2/Me",
                 "denied", response_detail(identity_status, identity_body))

suffix = f"__tp_preflight_{uuid.uuid4().hex}"
file_path = f"{landing}/{suffix}"
payload = b"otterworks tp preflight\n"
file_put_attempted = False
landing_api = "/api/2.0/fs/directories" + urllib.parse.quote(landing, safe="/")
file_api = "/api/2.0/fs/files" + urllib.parse.quote(file_path, safe="/")
req = urllib.request.Request(HOST + landing_api, headers={"Authorization": f"Bearer {TOKEN}"})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        manifest.add("files-get-directory", "List the landing volume directory", "Files API GET", "verified", f"HTTP {r.status}")
except urllib.error.HTTPError as exc:
    manifest.add("files-get-directory", "List the landing volume directory", "Files API GET",
                 "denied", http_error_detail(exc))
except Exception as exc:
    manifest.add("files-get-directory", "List the landing volume directory", "Files API GET", "denied", exception_detail(exc))
def reconcile_file():
    if not file_put_attempted:
        return
    req = urllib.request.Request(HOST + file_api, headers={"Authorization": f"Bearer {TOKEN}"}, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE",
                         "verified", f"HTTP {r.status}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE",
                         "verified", "file was absent")
        else:
            manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE",
                         "denied", exception_detail(exc))
    except Exception as exc:
        manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE",
                     "denied", exception_detail(exc))


register_cleanup("landing-file", reconcile_file)
try:
    req = urllib.request.Request(
        HOST + file_api + "?overwrite=true",
        data=payload,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/octet-stream",
        },
        method="PUT",
    )
    file_put_attempted = True
    with urllib.request.urlopen(req, timeout=30) as r:
        manifest.add("files-put", "Write a temporary landing file", "Files API PUT", "verified", f"HTTP {r.status}")
    req = urllib.request.Request(HOST + file_api, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        got = r.read()
        result = "verified" if got == payload else "denied"
        manifest.add("files-get-file", "Read the temporary landing file", "Files API GET", result, f"HTTP {r.status}, {len(got)} bytes")
except urllib.error.HTTPError as exc:
    manifest.add("files-put-get", "Write and read a temporary landing file", "Files API PUT/GET",
                 "denied", http_error_detail(exc))
except Exception as exc:
    manifest.add("files-put-get", "Write and read a temporary landing file", "Files API PUT/GET",
                 "denied", exception_detail(exc))
finally:
    if not file_put_attempted:
        manifest.add("files-delete", "Delete the temporary landing file", "Files API DELETE", "skipped", "PUT did not create a file")
    cleanup_all()

warehouse_probe = call("GET", "/api/2.0/sql/warehouses")
warehouse_id = configured_warehouse_id
discovered_warehouse_id = False
if not warehouse_id and 200 <= warehouse_probe[0] < 300 and isinstance(warehouse_probe[1], dict):
    for warehouse in warehouse_probe[1].get("warehouses", []):
        if warehouse.get("enable_serverless_compute") or warehouse.get("warehouse_type") == "PRO":
            warehouse_id = warehouse.get("id", "")
            discovered_warehouse_id = True
            break
if discovered_warehouse_id and not re.fullmatch(r"[A-Za-z0-9]+", warehouse_id):
    manifest.add(
        "warehouse-id",
        "Use the discovered SQL warehouse",
        "SQL Warehouses API",
        "denied",
        f"discovered warehouse id unusable: {warehouse_id!r}",
    )
    warehouse_id = ""
elif warehouse_id and not re.fullmatch(r"[A-Za-z0-9]+", warehouse_id):
    raise SystemExit(
        "DATABRICKS_SQL_WAREHOUSE_ID must match [A-Za-z0-9]+: "
        f"{warehouse_id!r}"
    )
schema = f"ow_tp_preflight_{uuid.uuid4().hex[:12]}"
schema_result = None


def reconcile_schema():
    if schema_result is not None and schema_result.state == "FAILED":
        manifest.add("uc-schema-delete", "Reconcile the temporary Unity Catalog schema",
                     "SQL Statement", "verified", f"{schema} was never created")
        return
    if schema_result is not None and schema_result.accepted:
        dropped = sql_call(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE", warehouse_id)
        manifest.add("uc-schema-delete", "Delete the temporary Unity Catalog schema",
                     "SQL Statement", "verified" if dropped.state == "SUCCEEDED" else "denied",
                     sql_detail(dropped))
        return
    lookup = sql_call(f"SHOW SCHEMAS IN {catalog}", warehouse_id)
    if lookup.state != "SUCCEEDED":
        manifest.add("uc-schema-delete", "Reconcile the temporary Unity Catalog schema",
                     "SQL Statement", "denied", sql_detail(lookup))
        return
    result = lookup.body.get("result", {})
    rows = result.get("data_array", []) if isinstance(result, dict) else []
    present = any(row and row[0] == schema for row in rows if isinstance(row, list))
    if not present:
        manifest.add("uc-schema-delete", "Reconcile the temporary Unity Catalog schema",
                     "SQL Statement", "verified", f"{schema} was absent after create")
        return
    dropped = sql_call(f"DROP SCHEMA IF EXISTS {catalog}.{schema} CASCADE", warehouse_id)
    manifest.add("uc-schema-delete", "Delete the temporary Unity Catalog schema",
                 "SQL Statement", "verified" if dropped.state == "SUCCEEDED" else "denied",
                 sql_detail(dropped))


register_cleanup("schema", reconcile_schema)
try:
    schema_result = sql_call(f"CREATE SCHEMA {catalog}.{schema}", warehouse_id)
    if schema_result.state == "SUCCEEDED":
        listed_schema = call("GET", f"/api/2.1/unity-catalog/schemas?catalog_name={urllib.parse.quote(catalog)}")
        manifest.add("uc-create-list", "Create and list a temporary Unity Catalog schema",
                     "SQL Statement + Unity Catalog APIs",
                     "verified" if 200 <= listed_schema[0] < 300 else "denied",
                     f"{sql_detail(schema_result)}; list HTTP {listed_schema[0]}")
    else:
        manifest.add("uc-create-list", "Create and list a temporary Unity Catalog schema",
                     "SQL Statement + Unity Catalog APIs", "denied", sql_detail(schema_result))
finally:
    cleanup_all()

job_name = f"ow_tp_preflight_{uuid.uuid4().hex[:8]}"
def reconcile_job():
    listed = call("GET", "/api/2.1/jobs/list?name=" + urllib.parse.quote(job_name))
    if not (200 <= listed[0] < 300) or not isinstance(listed[1], dict):
        manifest.add("jobs-delete", "Reconcile the temporary job", "Jobs API 2.1", "denied",
                     response_detail(listed[0], listed[1]))
        return
    matches = [item for item in listed[1].get("jobs", []) if item.get("settings", {}).get("name") == job_name]
    if not matches:
        manifest.add("jobs-delete", "Reconcile the temporary job", "Jobs API 2.1", "verified", f"{job_name} was absent after create")
        return
    deleted = call("POST", "/api/2.0/jobs/delete", {"job_id": matches[0].get("job_id")})
    manifest.add("jobs-delete", "Delete the temporary job", "Jobs API 2.0",
                 "verified" if 200 <= deleted[0] < 300 else "denied", response_detail(deleted[0], deleted[1]))


register_cleanup("job", reconcile_job)
try:
    job = call("POST", "/api/2.1/jobs/create", {"name": job_name, "tasks": [{"task_key": "noop", "notebook_task": {"notebook_path": "/Shared/ow_tp/preflight"}}]})
    if 200 <= job[0] < 300:
        listed_jobs = call("GET", "/api/2.1/jobs/list?name=" + urllib.parse.quote(job_name))
        manifest.add("jobs-create-list", "Create and list a temporary job", "Jobs API 2.1",
                     "verified" if 200 <= listed_jobs[0] < 300 else "denied",
                     f"create HTTP {job[0]}, list HTTP {listed_jobs[0]}")
    else:
        manifest.add("jobs-create-list", "Create and list a temporary job", "Jobs API 2.1", "denied",
                     response_detail(job[0], job[1]))
finally:
    cleanup_all()

scope_name = f"ow_tp_preflight_{uuid.uuid4().hex[:8]}"
def reconcile_scope():
    listed = call("GET", "/api/2.0/secrets/scopes/list")
    if not (200 <= listed[0] < 300) or not isinstance(listed[1], dict):
        manifest.add("secret-scope-delete", "Reconcile the temporary secret scope", "Secrets API 2.0",
                     "denied", response_detail(listed[0], listed[1]))
        return
    matches = [item for item in listed[1].get("scopes", []) if item.get("name") == scope_name]
    if not matches:
        manifest.add("secret-scope-delete", "Reconcile the temporary secret scope", "Secrets API 2.0",
                     "verified", f"{scope_name} was absent after create")
        return
    deleted = call("POST", "/api/2.0/secrets/scopes/delete", {"scope": scope_name})
    manifest.add("secret-scope-delete", "Delete the temporary secret scope", "Secrets API 2.0",
                 "verified" if 200 <= deleted[0] < 300 else "denied", response_detail(deleted[0], deleted[1]))


register_cleanup("scope", reconcile_scope)
try:
    scope = call("POST", "/api/2.0/secrets/scopes/create", {"scope": scope_name})
    if 200 <= scope[0] < 300:
        manifest.add("secret-scope", "Create and delete a temporary secret scope", "Secrets API 2.0",
                     "verified", f"create HTTP {scope[0]}")
    else:
        manifest.add("secret-scope", "Create and delete a temporary secret scope", "Secrets API 2.0",
                     "denied", response_detail(scope[0], scope[1]))
finally:
    cleanup_all()

if 200 <= warehouse_probe[0] < 300 and isinstance(warehouse_probe[1], dict):
    serverless = [w for w in warehouse_probe[1].get("warehouses", []) if w.get("enable_serverless_compute")]
    summary = [
        {
            "id": w.get("id"),
            "name": w.get("name"),
            "state": w.get("state"),
            "enable_serverless_compute": w.get("enable_serverless_compute"),
        }
        for w in serverless[:3]
    ]
    manifest.add(
        "serverless-warehouse",
        "An existing serverless SQL warehouse is available",
        "SQL Warehouses API",
        "verified" if serverless else "denied",
        json.dumps(summary) if summary else "no warehouse with enable_serverless_compute",
    )
else:
    manifest.add("serverless-warehouse", "An existing serverless SQL warehouse is available", "SQL Warehouses API", "denied", f"HTTP {warehouse_probe[0]}")

raise SystemExit(manifest.write("databricks"))

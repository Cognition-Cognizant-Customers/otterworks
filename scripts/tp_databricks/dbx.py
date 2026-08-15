#!/usr/bin/env python3
"""Shared Databricks driver for the tech-partnerships migration track.

One place for the four things every converted job and recon script needs:
run SQL on the existing serverless warehouse, upload a legacy input file to the
landing volume, trigger a job and wait for it, and enumerate what the demo owns
in the shared workspace.

Credentials come from DATABRICKS_HOST/DATABRICKS_TOKEN, falling back to
DATABRICKS_DEMO_HOST/DATABRICKS_DEMO_TOKEN. Nothing here creates compute.

CLI:
    dbx.py sql "SELECT 1"                       # rows as TSV (--json for JSON)
    dbx.py upload <local_file> <volume_relpath>
    dbx.py deploy-notebook <local_file> <name>
    dbx.py run-job <job_name> [k=v ...]
    dbx.py inventory                            # every ow_tp-prefixed object
    dbx.py teardown-check                        # exit 1 if any remain
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PREFIX = "ow_tp"
CATALOG = os.environ.get("OW_TP_CATALOG", PREFIX)
WAREHOUSE_NAME = os.environ.get("OW_TP_WAREHOUSE", "Serverless Starter Warehouse")
PIPELINE_ROOT = f"/Shared/{PREFIX}"


class DatabricksError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_code = error_code


def _host() -> str:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_DEMO_HOST")
    if not host:
        raise DatabricksError("DATABRICKS_HOST (or DATABRICKS_DEMO_HOST) is not set")
    return host.rstrip("/")


def _token() -> str:
    token = os.environ.get("DATABRICKS_TOKEN") or os.environ.get("DATABRICKS_DEMO_TOKEN")
    if not token:
        raise DatabricksError("DATABRICKS_TOKEN (or DATABRICKS_DEMO_TOKEN) is not set")
    return token


def request(method: str, path: str, body: dict | None = None, raw: bytes | None = None) -> dict:
    """Call the Databricks REST API and return the decoded JSON body."""
    headers = {"Authorization": f"Bearer {_token()}"}
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        headers["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(_host() + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:  # surface the API message, not just the code
        payload = exc.read().decode()
        try:
            error_code = json.loads(payload).get("error_code")
        except json.JSONDecodeError:
            error_code = None
        raise DatabricksError(
            f"{method} {path} -> {exc.code}: {payload[:800]}",
            status=exc.code,
            error_code=error_code,
        ) from exc
    return json.loads(payload) if payload else {}


def warehouse_id() -> str:
    for warehouse in request("GET", "/api/2.0/sql/warehouses").get("warehouses", []):
        if warehouse["name"] == WAREHOUSE_NAME:
            return warehouse["id"]
    raise DatabricksError(f"serverless SQL warehouse {WAREHOUSE_NAME!r} not found (never create one)")


def _statement_rows(result: dict) -> list[list[str]]:
    """Collect all result chunks returned by Statement Execution."""
    statement_result = result.get("result") or {}
    rows = list(statement_result.get("data_array") or [])
    manifest = result.get("manifest") or {}
    total_chunks = manifest.get("total_chunk_count")
    next_link = statement_result.get("next_chunk_internal_link")
    while next_link:
        chunk = request("GET", next_link)
        chunk_result = chunk.get("result") or chunk
        rows.extend(chunk_result.get("data_array") or [])
        next_link = chunk_result.get("next_chunk_internal_link")
    if total_chunks is not None and total_chunks > 1 and not statement_result.get("next_chunk_internal_link"):
        raise DatabricksError("statement result is chunked but did not provide a next chunk link")
    return rows


def sql(
    statement: str,
    catalog: str | None = None,
    schema: str | None = None,
    timeout_s: int = 1800,
) -> list[list[str]]:
    """Execute one statement on the serverless warehouse and return its rows."""
    body = {
        "warehouse_id": warehouse_id(),
        "statement": statement,
        "wait_timeout": "50s",
        "on_wait_timeout": "CONTINUE",
    }
    if catalog:
        body["catalog"] = catalog
    if schema:
        body["schema"] = schema
    result = request("POST", "/api/2.0/sql/statements", body)
    statement_id = result["statement_id"]
    deadline = time.time() + timeout_s
    while result["status"]["state"] in ("PENDING", "RUNNING"):
        if time.time() >= deadline:
            raise DatabricksError(f"statement {statement_id} still running after {timeout_s}s")
        time.sleep(2)
        result = request("GET", f"/api/2.0/sql/statements/{statement_id}")
    state = result["status"]["state"]
    if state != "SUCCEEDED":
        message = result["status"].get("error", {}).get("message", state)
        raise DatabricksError(f"statement failed ({state}): {message}\n  {statement[:400]}")
    return _statement_rows(result)


def sql_scalar(statement: str) -> str | None:
    rows = sql(statement)
    return rows[0][0] if rows and rows[0] else None


def _list_jobs(name: str | None = None) -> list[dict]:
    """List all jobs, following the Jobs API pagination token."""
    jobs = []
    page_token = None
    while True:
        params = {"limit": "100"}
        if name:
            params["name"] = name
        if page_token:
            params["page_token"] = page_token
        query = urllib.parse.urlencode(params)
        result = request("GET", f"/api/2.2/jobs/list?{query}")
        jobs.extend(result.get("jobs", []))
        if not result.get("next_page_token"):
            return jobs
        page_token = result["next_page_token"]


def upload(local_path: str, volume_relpath: str) -> str:
    """Upload a local file into the landing volume, overwriting in place."""
    target = f"/Volumes/{CATALOG}/bronze/landing/{volume_relpath.lstrip('/')}"
    with open(local_path, "rb") as handle:
        payload = handle.read()
    request(
        "PUT",
        f"/api/2.0/fs/files{urllib.parse.quote(target)}?overwrite=true",
        raw=payload,
    )
    return target


def deploy_notebook(local_path: str, name: str) -> str:
    """Import a notebook source file under the demo's workspace directory."""
    with open(local_path, "rb") as handle:
        content = base64.b64encode(handle.read()).decode()
    path = f"{PIPELINE_ROOT}/{name}"
    request("POST", "/api/2.0/workspace/mkdirs", {"path": PIPELINE_ROOT})
    request(
        "POST",
        "/api/2.0/workspace/import",
        {"path": path, "format": "SOURCE", "language": "PYTHON", "content": content, "overwrite": True},
    )
    return path


def job_id(name: str) -> int:
    for job in _list_jobs(name):
        if job["settings"]["name"] == name:
            return job["job_id"]
    raise DatabricksError(f"job {name!r} not found: apply infrastructure/terraform-databricks first")


def run_job(name: str, params: dict[str, str] | None = None, timeout_s: int = 1800) -> dict:
    """Trigger a job by name, block until it terminates, and return the run."""
    body: dict = {"job_id": job_id(name)}
    if params:
        body["job_parameters"] = params
    run_id = request("POST", "/api/2.2/jobs/run-now", body)["run_id"]
    deadline = time.time() + timeout_s
    while True:
        run = request("GET", f"/api/2.2/jobs/runs/get?run_id={run_id}")
        state = run.get("state", {})
        if state.get("life_cycle_state") in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
            return run
        if time.time() > deadline:
            raise DatabricksError(f"run {run_id} of {name} still {state.get('life_cycle_state')} after {timeout_s}s")
        time.sleep(10)


def inventory() -> dict[str, list[str]]:
    """Everything the demo owns in the shared workspace, by prefix."""
    catalogs = [
        c["name"] for c in request("GET", "/api/2.1/unity-catalog/catalogs").get("catalogs", [])
        if c["name"].startswith(PREFIX)
    ]
    jobs = [j["settings"]["name"] for j in _list_jobs() if j["settings"]["name"].startswith(PREFIX)]
    scopes = [
        s["name"] for s in request("GET", "/api/2.0/secrets/scopes/list").get("scopes", [])
        if s["name"].startswith(PREFIX)
    ]
    try:
        request("GET", f"/api/2.0/workspace/get-status?path={urllib.parse.quote(PIPELINE_ROOT)}")
        directories = [PIPELINE_ROOT]
    except DatabricksError as exc:
        if exc.status == 404 or exc.error_code == "RESOURCE_DOES_NOT_EXIST":
            directories = []
        else:
            raise
    return {"catalogs": catalogs, "jobs": jobs, "secret_scopes": scopes, "directories": directories}


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    command, args = argv[0], argv[1:]
    if command == "sql":
        as_json = "--json" in args
        rows = sql(" ".join(a for a in args if a != "--json"))
        print(json.dumps(rows, indent=2) if as_json else "\n".join("\t".join(map(str, r)) for r in rows))
    elif command == "upload":
        print(upload(args[0], args[1]))
    elif command == "deploy-notebook":
        print(deploy_notebook(args[0], args[1]))
    elif command == "run-job":
        params = dict(a.split("=", 1) for a in args[1:])
        run = run_job(args[0], params)
        result = run.get("state", {}).get("result_state")
        print(json.dumps({"run_id": run.get("run_id"), "result_state": result, "url": run.get("run_page_url")}, indent=2))
        return 0 if result == "SUCCESS" else 1
    elif command in ("inventory", "teardown-check"):
        found = inventory()
        print(json.dumps(found, indent=2))
        if command == "teardown-check" and any(found.values()):
            print(f"teardown incomplete: {PREFIX}-prefixed objects still present", file=sys.stderr)
            return 1
    else:
        print(f"unknown command {command!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))

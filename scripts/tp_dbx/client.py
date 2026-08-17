#!/usr/bin/env python3
"""Minimal Databricks REST/SQL client shared by the showcase tooling.

Stdlib only, mirroring scripts/tp_preflight/databricks.py: the demo VMs have no
guaranteed databricks-sdk, and every call here must be auditable in the PR.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

NS_RE = re.compile(r"[a-z0-9_]{1,24}")
IDENT_RE = re.compile(r"[A-Za-z0-9_]+")


class DbxError(RuntimeError):
    pass


def require_ns(ns: str) -> str:
    if not NS_RE.fullmatch(ns):
        raise SystemExit(f"namespace must match [a-z0-9_]{{1,24}}: {ns!r}")
    return ns


def require_ident(value: str, label: str) -> str:
    if not IDENT_RE.fullmatch(value):
        raise SystemExit(f"{label} must match [A-Za-z0-9_]+: {value!r}")
    return value


@dataclass
class SqlResult:
    state: str
    columns: list[str]
    rows: list[list]
    error: str

    @property
    def ok(self) -> bool:
        return self.state == "SUCCEEDED"

    def scalar(self):
        return self.rows[0][0] if self.rows and self.rows[0] else None

    def dicts(self) -> list[dict]:
        return [dict(zip(self.columns, row)) for row in self.rows]


class Databricks:
    def __init__(self, host: str | None = None, token: str | None = None, warehouse_id: str | None = None):
        raw_host = host or os.environ.get("DATABRICKS_DEMO_HOST", "")
        self.token = token or os.environ.get("DATABRICKS_DEMO_TOKEN", "")
        if not raw_host or not self.token:
            raise SystemExit("DATABRICKS_DEMO_HOST and DATABRICKS_DEMO_TOKEN are required")
        parsed = urllib.parse.urlparse(raw_host)
        if parsed.scheme != "https" or not parsed.hostname:
            raise SystemExit("DATABRICKS_DEMO_HOST must be an https workspace URL")
        self.host = raw_host.rstrip("/")
        self._warehouse_id = warehouse_id or os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID", "")

    # --- REST ---------------------------------------------------------------
    def call(self, method: str, path: str, body=None, raw_body: bytes | None = None,
             content_type: str = "application/json") -> tuple[int, dict]:
        data = raw_body if raw_body is not None else (None if body is None else json.dumps(body).encode())
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.host + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                payload = response.read()
                if not payload:
                    return response.status, {}
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    return response.status, {"_raw": payload[:400].decode(errors="replace")}
                return response.status, parsed if isinstance(parsed, dict) else {"_list": parsed}
        except urllib.error.HTTPError as exc:
            text = exc.read().decode(errors="replace")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {"_raw": text[:400]}
            return exc.code, parsed if isinstance(parsed, dict) else {"_list": parsed}

    def ok(self, method: str, path: str, body=None) -> dict:
        status, payload = self.call(method, path, body)
        if not 200 <= status < 300:
            raise DbxError(f"{method} {path} -> HTTP {status}: {json.dumps(payload)[:400]}")
        return payload

    def list_all(self, path: str, *keys: str, page_size: int = 100) -> list[dict]:
        """Shared workspace: a namespace's alert or dashboard can sit past the first
        page, and a lookup that stops there duplicates on create and misses on
        teardown, so follow next_page_token to exhaustion. Several keys are accepted
        because the alerts API pages under `alerts` while its docs and SDK say
        `results`."""
        separator = "&" if "?" in path else "?"
        page_token = ""
        items: list[dict] = []
        while True:
            url = f"{path}{separator}page_size={page_size}"
            if page_token:
                url += f"&page_token={urllib.parse.quote(page_token)}"
            payload = self.ok("GET", url)
            for key in keys:
                if key in payload:
                    items.extend(payload[key])
                    break
            page_token = payload.get("next_page_token", "")
            if not page_token:
                return items

    # --- SQL ----------------------------------------------------------------
    @property
    def warehouse_id(self) -> str:
        if self._warehouse_id:
            return require_ident(self._warehouse_id, "warehouse id")
        payload = self.ok("GET", "/api/2.0/sql/warehouses")
        for warehouse in payload.get("warehouses", []):
            if warehouse.get("enable_serverless_compute"):
                self._warehouse_id = require_ident(str(warehouse.get("id", "")), "warehouse id")
                return self._warehouse_id
        raise DbxError("no serverless SQL warehouse available; refusing to create clusters")

    def sql(self, statement: str, wait: str = "50s", tries: int = 40) -> SqlResult:
        status, payload = self.call("POST", "/api/2.0/sql/statements", {
            "statement": statement,
            "warehouse_id": self.warehouse_id,
            "wait_timeout": wait,
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
        })
        if not 200 <= status < 300:
            return SqlResult("HTTP_ERROR", [], [], f"HTTP {status}: {json.dumps(payload)[:400]}")
        statement_id = payload.get("statement_id")
        state = payload.get("status", {}).get("state", "UNKNOWN")
        for _ in range(tries):
            if state in {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"} or not statement_id:
                break
            time.sleep(3)
            quoted = urllib.parse.quote(str(statement_id), safe="")
            _, payload = self.call("GET", f"/api/2.0/sql/statements/{quoted}")
            state = payload.get("status", {}).get("state", "UNKNOWN")
        error = payload.get("status", {}).get("error", {})
        message = error.get("message", "") if isinstance(error, dict) else str(error)
        columns = [c.get("name", "") for c in payload.get("manifest", {}).get("schema", {}).get("columns", [])]
        rows = payload.get("result", {}).get("data_array") or []
        return SqlResult(state, columns, rows, message)

    def sql_ok(self, statement: str) -> SqlResult:
        result = self.sql(statement)
        if not result.ok:
            raise DbxError(f"SQL failed ({result.state}): {result.error}\n  statement: {statement[:300]}")
        return result

    # --- Files (Unity Catalog volumes) -------------------------------------
    def put_file(self, volume_path: str, payload: bytes) -> None:
        quoted = urllib.parse.quote(volume_path, safe="/")
        status, body = self.call(
            "PUT", f"/api/2.0/fs/files{quoted}?overwrite=true",
            raw_body=payload, content_type="application/octet-stream",
        )
        if not 200 <= status < 300:
            raise DbxError(f"PUT {volume_path} -> HTTP {status}: {json.dumps(body)[:300]}")

    def delete_file(self, volume_path: str) -> int:
        quoted = urllib.parse.quote(volume_path, safe="/")
        status, _ = self.call("DELETE", f"/api/2.0/fs/files{quoted}")
        return status

    def delete_dir(self, volume_path: str) -> int:
        """Volume directories need the directories endpoint; the files one leaves
        them behind empty."""
        quoted = urllib.parse.quote(volume_path, safe="/")
        status, _ = self.call("DELETE", f"/api/2.0/fs/directories{quoted}")
        return status

    def list_dir(self, volume_path: str) -> list[dict]:
        quoted = urllib.parse.quote(volume_path, safe="/")
        status, payload = self.call("GET", f"/api/2.0/fs/directories{quoted}")
        if status == 404:
            return []
        if not 200 <= status < 300:
            raise DbxError(f"GET {volume_path} -> HTTP {status}: {json.dumps(payload)[:300]}")
        return payload.get("contents", [])

    # --- Workspace ----------------------------------------------------------
    def import_notebook(self, path: str, source: str, language: str = "PYTHON") -> None:
        import base64

        self.ok("POST", "/api/2.0/workspace/mkdirs", {"path": path.rsplit("/", 1)[0]})
        self.ok("POST", "/api/2.0/workspace/import", {
            "path": path,
            "format": "SOURCE",
            "language": language,
            "overwrite": True,
            "content": base64.b64encode(source.encode()).decode(),
        })

    # --- Jobs ---------------------------------------------------------------
    def find_job(self, name: str) -> dict | None:
        payload = self.ok("GET", "/api/2.1/jobs/list?name=" + urllib.parse.quote(name))
        for job in payload.get("jobs", []):
            if job.get("settings", {}).get("name") == name:
                return job
        return None

    def upsert_job(self, settings: dict) -> int:
        existing = self.find_job(settings["name"])
        if existing:
            job_id = int(existing["job_id"])
            self.ok("POST", "/api/2.1/jobs/reset", {"job_id": job_id, "new_settings": settings})
            return job_id
        return int(self.ok("POST", "/api/2.1/jobs/create", settings)["job_id"])

    def run_job(self, job_id: int, params: dict | None = None) -> int:
        body = {"job_id": job_id}
        if params:
            body["job_parameters"] = params
        return int(self.ok("POST", "/api/2.1/jobs/run-now", body)["run_id"])

    def wait_run(self, run_id: int, timeout_s: int = 900) -> dict:
        deadline = time.time() + timeout_s
        while True:
            run = self.ok("GET", f"/api/2.1/jobs/runs/get?run_id={run_id}")
            state = run.get("state", {})
            if state.get("life_cycle_state") in {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}:
                return run
            if time.time() > deadline:
                raise DbxError(f"run {run_id} did not finish within {timeout_s}s")
            time.sleep(10)

    def run_url(self, run_id: int) -> str:
        return f"{self.host}/jobs/runs/{run_id}"

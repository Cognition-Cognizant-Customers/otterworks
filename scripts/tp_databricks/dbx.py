#!/usr/bin/env python3
"""Thin Databricks REST helper for the tech-partnerships migration units.

Children use this for SQL, volume uploads/downloads, notebook deploys, and job
runs instead of hand-rolling REST calls. Auth comes from DATABRICKS_HOST /
DATABRICKS_TOKEN (fall back to DATABRICKS_DEMO_HOST / DATABRICKS_DEMO_TOKEN).

Usage:
  dbx.py sql "SELECT 1" [--warehouse-id ID]
  dbx.py put <local-path> <volume-path>       # /Volumes/... absolute path
  dbx.py get <volume-path> <local-path>
  dbx.py delete <volume-path>
  dbx.py ls <volume-dir>
  dbx.py import-notebook <local-path> <workspace-path>
  dbx.py run-job <job-id> [--param k=v ...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request


def _env(*names: str) -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    raise SystemExit(f"missing env: one of {names} required")


HOST = _env("DATABRICKS_HOST", "DATABRICKS_DEMO_HOST").rstrip("/")
TOKEN = _env("DATABRICKS_TOKEN", "DATABRICKS_DEMO_TOKEN")


def _req(method: str, path: str, body: bytes | None = None,
         content_type: str = "application/json") -> tuple[int, bytes]:
    req = urllib.request.Request(HOST + path, data=body, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    if body is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _json(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    status, data = _req(method, path, body)
    if status >= 300:
        raise SystemExit(f"{method} {path} -> HTTP {status}: {data.decode(errors='replace')[:500]}")
    return json.loads(data) if data else {}


def serverless_warehouse_id() -> str:
    ws = _json("GET", "/api/2.0/sql/warehouses").get("warehouses", [])
    for w in ws:
        if w.get("enable_serverless_compute"):
            return w["id"]
    raise SystemExit("no serverless SQL warehouse available")


def sql(statement: str, warehouse_id: str | None = None) -> dict:
    wid = warehouse_id or os.environ.get("DATABRICKS_SQL_WAREHOUSE_ID") or serverless_warehouse_id()
    out = _json("POST", "/api/2.0/sql/statements", {
        "statement": statement, "warehouse_id": wid, "wait_timeout": "50s",
        "on_wait_timeout": "CONTINUE",
    })
    while out.get("status", {}).get("state") in ("PENDING", "RUNNING"):
        time.sleep(2)
        out = _json("GET", f"/api/2.0/sql/statements/{out['statement_id']}")
    return out


def _vol(path: str) -> str:
    if not path.startswith("/Volumes/"):
        raise SystemExit("volume path must start with /Volumes/")
    return "/api/2.0/fs/files" + urllib.parse.quote(path)


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sql"); s.add_argument("statement"); s.add_argument("--warehouse-id")
    s = sub.add_parser("put"); s.add_argument("local"); s.add_argument("remote")
    s = sub.add_parser("get"); s.add_argument("remote"); s.add_argument("local")
    s = sub.add_parser("delete"); s.add_argument("remote")
    s = sub.add_parser("ls"); s.add_argument("remote")
    s = sub.add_parser("import-notebook"); s.add_argument("local"); s.add_argument("remote")
    s = sub.add_parser("run-job"); s.add_argument("job_id"); s.add_argument("--param", action="append", default=[])
    a = p.parse_args()

    if a.cmd == "sql":
        out = sql(a.statement, a.warehouse_id)
        print(json.dumps(out, indent=2))
        return 0 if out.get("status", {}).get("state") == "SUCCEEDED" else 1
    if a.cmd == "put":
        with open(a.local, "rb") as f:
            status, data = _req("PUT", _vol(a.remote) + "?overwrite=true", f.read(),
                                "application/octet-stream")
        print(f"PUT {a.remote}: HTTP {status}")
        return 0 if status < 300 else 1
    if a.cmd == "get":
        status, data = _req("GET", _vol(a.remote))
        if status >= 300:
            print(f"GET {a.remote}: HTTP {status}", file=sys.stderr); return 1
        with open(a.local, "wb") as f:
            f.write(data)
        print(f"GET {a.remote}: {len(data)} bytes")
        return 0
    if a.cmd == "delete":
        status, _ = _req("DELETE", _vol(a.remote))
        print(f"DELETE {a.remote}: HTTP {status}")
        return 0 if status < 300 else 1
    if a.cmd == "ls":
        out = _json("GET", "/api/2.0/fs/directories" + urllib.parse.quote(a.remote))
        for e in out.get("contents", []):
            print(f"{e.get('file_size', '-'):>10}  {e['path']}")
        return 0
    if a.cmd == "import-notebook":
        import base64
        with open(a.local, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        _json("POST", "/api/2.0/workspace/import", {
            "path": a.remote, "format": "SOURCE", "language": "PYTHON",
            "content": content, "overwrite": True,
        })
        print(f"imported {a.remote}")
        return 0
    if a.cmd == "run-job":
        params = dict(kv.split("=", 1) for kv in a.param)
        run = _json("POST", "/api/2.2/jobs/run-now",
                    {"job_id": int(a.job_id), "job_parameters": params} if params
                    else {"job_id": int(a.job_id)})
        run_id = run["run_id"]
        while True:
            out = _json("GET", f"/api/2.2/jobs/runs/get?run_id={run_id}")
            state = out.get("status", {}).get("state") or out.get("state", {}).get("life_cycle_state")
            if state in ("TERMINATED", "SKIPPED", "INTERNAL_ERROR"):
                result = out.get("status", {}).get("termination_details", {}).get("code") \
                    or out.get("state", {}).get("result_state")
                print(f"run {run_id}: {state} / {result}")
                return 0 if result in ("SUCCESS",) else 1
            time.sleep(10)
    return 2


if __name__ == "__main__":
    sys.exit(main())

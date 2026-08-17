#!/usr/bin/env python3
"""Atomic, byte-transparent CUSTBILL landing and bronze registration."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest_sql as S
from client import Databricks, DbxError, require_ident, require_ns


def names(args) -> S.Names:
    return S.Names(catalog=require_ident(args.catalog, "catalog"), ns=require_ns(args.ns))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def eligible_drop_names(entries: list[dict]) -> list[str]:
    names = set()
    for entry in entries:
        name = str(entry.get("name") or entry.get("path", "").rsplit("/", 1)[-1])
        if not name.startswith(S.DROP_GLOB_PREFIX) or not name.endswith(S.DROP_GLOB_SUFFIX):
            continue
        if any(name.endswith(suffix) for suffix in S.IN_PROGRESS_SUFFIXES):
            continue
        names.add(name)
    sidecars = {
        str(entry.get("name") or entry.get("path", "").rsplit("/", 1)[-1])
        for entry in entries
    }
    return sorted(name for name in names if f"{name}.sha256" in sidecars)


def normalise_source_name(name: str, strip_suffix: str) -> str:
    value = name
    if strip_suffix:
        if not value.endswith(strip_suffix):
            raise ValueError(f"source file {name!r} does not end with --strip-suffix {strip_suffix!r}")
        value = value[: -len(strip_suffix)]
    if not value.startswith("CUSTBILL") or not value.endswith(".dat") or "/" in value:
        raise ValueError(f"normalisation produced an unexpected file name: {value!r}")
    return value


def cmd_send_drop(dbx: Databricks, args) -> int:
    n = names(args)
    source = Path(args.source)
    if not source.is_dir():
        raise SystemExit(f"source directory not found: {source}")
    for path in sorted(source.iterdir()):
        if not path.is_file() or any(path.name.endswith(s) for s in S.IN_PROGRESS_SUFFIXES):
            continue
        if not path.name.startswith("CUSTBILL"):
            continue
        target = normalise_source_name(path.name, args.strip_suffix)
        payload = path.read_bytes()
        digest = sha256(payload)
        part = f"{n.drop_dir}/{target}.part"
        final = f"{n.drop_dir}/{target}"
        dbx.put_file(part, payload)
        dbx.put_file(final, payload)
        dbx.delete_file(part)
        dbx.put_file(f"{n.drop_dir}/{target}.sha256", digest.encode("ascii"))
        print(f"{target} size={len(payload)} sha256={digest}")
    return 0


def _existing_pairs(dbx: Databricks, n: S.Names) -> set[tuple[str, str]]:
    result = dbx.sql_ok(f"SELECT source_file, content_sha256 FROM {n.bronze}")
    return {(str(row[0]), str(row[1])) for row in result.rows}


def publish(dbx, n: S.Names, run_id: str) -> dict | None:
    entries = dbx.list_dir(n.drop_dir)
    names_to_publish = eligible_drop_names(entries)
    if not names_to_publish:
        print(f"publish no-op: drop is empty for ns={n.ns}")
        return None
    verified = []
    for name in names_to_publish:
        payload = dbx.get_file(f"{n.drop_dir}/{name}")
        digest = sha256(payload)
        sidecar = dbx.get_file(f"{n.drop_dir}/{name}.sha256").decode("ascii").strip()
        if sidecar != digest:
            raise RuntimeError(f"drop completion marker mismatch for {name}")
        verified.append((name, payload, digest))
    dbx.sql_ok(S.create_bronze(n))
    existing = _existing_pairs(dbx, n)
    rows, objects = [], []
    for name, payload, digest in verified:
        if (name, digest) in existing:
            continue
        landed_path = f"{n.run_data_dir(run_id)}/{name}"
        dbx.put_file(landed_path, payload)
        if sha256(dbx.get_file(landed_path)) != digest:
            raise RuntimeError(f"byte mismatch after publishing {name}")
        objects.append({
            "source_file": name, "byte_size": len(payload), "content_sha256": digest,
            "landed_path": landed_path,
        })
        rows.append({
            **objects[-1], "commit_id": run_id, "ingest_run_id": run_id,
        })
    if not objects:
        print(f"publish no-op: all drop objects already registered for ns={n.ns}")
        return None
    marker = {
        "run_id": run_id,
        "committed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "objects": objects,
    }
    dbx.put_file(n.commit_path(run_id), json.dumps(marker, sort_keys=True).encode())
    dbx.sql_ok(S.merge_bronze(n, rows))
    print(f"published {len(objects)} object(s), commit={n.commit_path(run_id)}")
    return marker


def cmd_publish(dbx, args) -> int:
    publish(dbx, names(args), args.run_id)
    return 0


def _raw_result(result):
    return result.dicts() if result.ok else {"state": result.state, "error": result.error}


def _walk_files(dbx, path: str) -> list[str]:
    found = []
    for entry in dbx.list_dir(path):
        child = entry.get("path", "")
        if entry.get("is_directory") or entry.get("type") == "DIRECTORY":
            found.extend(_walk_files(dbx, child))
        elif child:
            found.append(child)
    return found


def cmd_recon_collect(dbx, args) -> int:
    n = names(args)
    sql_results = []
    for statement in (S.recon_inventory(n), S.recon_counts(n),
                      S.recon_null_attribution(n), S.recon_duplicates(n)):
        sql_results.append({"statement": statement, "result": _raw_result(dbx.sql_ok(statement))})
    volume = {"objects": [], "commits": []}
    for path in _walk_files(dbx, n.data_dir):
        if path.endswith(".dat"):
            payload = dbx.get_file(path)
            volume["objects"].append({"path": path, "byte_size": len(payload), "content_sha256": sha256(payload)})
    for path in _walk_files(dbx, n.commit_dir):
        if path.endswith(".json"):
            marker = json.loads(dbx.get_file(path))
            checked = []
            for obj in marker.get("objects", []):
                payload = dbx.get_file(obj["landed_path"])
                checked.append({"path": obj["landed_path"], "byte_size": len(payload), "content_sha256": sha256(payload)})
            volume["commits"].append({"path": path, "marker": marker, "objects": checked})
    output = {"namespace": n.ns, "catalog": n.catalog, "sql": sql_results, "volume": volume}
    text = json.dumps(output, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n")
    print(text)
    return 0


def notebook_source(n: S.Names) -> str:
    embedded = "\n\n".join([
        inspect.getsource(S.Names),
        inspect.getsource(S.quote),
        inspect.getsource(S.create_bronze),
        inspect.getsource(S.merge_bronze),
    ])
    embedded = (
        "IN_PROGRESS_SUFFIXES = " + repr(S.IN_PROGRESS_SUFFIXES) + "\n"
        "DROP_GLOB_PREFIX = " + repr(S.DROP_GLOB_PREFIX) + "\n"
        "DROP_GLOB_SUFFIX = " + repr(S.DROP_GLOB_SUFFIX) + "\n\n" + embedded
    )
    return f'''# Databricks notebook source
import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

dbutils.widgets.text("ns", "{n.ns}")
dbutils.widgets.text("catalog", "{n.catalog}")
dbutils.widgets.text("run_id", "")
ns = dbutils.widgets.get("ns")
catalog = dbutils.widgets.get("catalog")
run_id = dbutils.widgets.get("run_id") or "local-" + uuid.uuid4().hex
landing = f"/Volumes/{{catalog}}/bronze/landing/{{ns}}"
drop_dir = landing + "/drop"
data_dir = landing + "/ingest/data/" + run_id
commit_dir = landing + "/ingest/_commits"

# BEGIN EMBEDDED INGEST_SQL
{embedded}
# END EMBEDDED INGEST_SQL
n = Names(catalog=catalog, ns=ns)

def digest(path):
    with open(path, "rb") as handle:
        data = handle.read()
    return data, hashlib.sha256(data).hexdigest()

entries = os.listdir(drop_dir) if os.path.isdir(drop_dir) else []
names_to_publish = sorted(
    name for name in entries
    if name.startswith(DROP_GLOB_PREFIX)
    and name.endswith(DROP_GLOB_SUFFIX)
    and name + ".sha256" in entries
)
verified = []
for name in names_to_publish:
    data, content_sha256 = digest(drop_dir + "/" + name)
    with open(drop_dir + "/" + name + ".sha256", "rb") as handle:
        marker_sha256 = handle.read().decode("ascii").strip()
    if marker_sha256 != content_sha256:
        raise RuntimeError("drop completion marker mismatch for " + name)
    verified.append((name, data, content_sha256))
if verified:
    spark.sql(create_bronze(n))
objects = []
rows = []
if verified:
    existing = {{
        (str(row[0]), str(row[1]))
        for row in spark.sql(f"SELECT source_file, content_sha256 FROM {{n.bronze}}").collect()
    }}
    for name, data, content_sha256 in verified:
        if (name, content_sha256) in existing:
            continue
        os.makedirs(data_dir, exist_ok=True)
        os.makedirs(commit_dir, exist_ok=True)
        landed = data_dir + "/" + name
        with open(landed, "wb") as handle:
            handle.write(data)
        check, check_sha = digest(landed)
        if check != data or check_sha != content_sha256:
            raise RuntimeError("byte mismatch after publishing " + name)
        obj = {{"source_file": name, "byte_size": len(data), "content_sha256": content_sha256, "landed_path": landed}}
        objects.append(obj)
        rows.append({{**obj, "commit_id": run_id, "ingest_run_id": run_id}})
if objects:
    required = ("source_file", "byte_size", "content_sha256", "landed_path", "commit_id", "ingest_run_id")
    for row in rows:
        for key in required:
            if row.get(key) is None or row.get(key) == "":
                raise ValueError("refusing to register a bronze row with missing " + key)
    marker = {{"run_id": run_id, "committed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "objects": objects}}
    with open(commit_dir + "/" + run_id + ".json", "w", encoding="utf-8") as handle:
        json.dump(marker, handle, sort_keys=True)
    spark.sql(merge_bronze(n, rows))
print(json.dumps({{"run_id": run_id, "published_objects": len(objects), "commit": commit_dir + "/" + run_id + ".json" if objects else None}}, sort_keys=True))
'''


def cmd_deploy_job(dbx, args) -> int:
    n = names(args)
    dbx.import_notebook(n.notebook, notebook_source(n))
    settings = {
        "name": n.job,
        "max_concurrent_runs": 1,
        "queue": {"enabled": True},
        "tags": {"project": "otterworks-tp", "unit": "dbx-ingest", "namespace": n.ns},
        "timeout_seconds": args.timeout_seconds,
        "tasks": [{"task_key": "ingest", "notebook_task": {
            "notebook_path": n.notebook,
            "base_parameters": {"ns": n.ns, "catalog": n.catalog, "run_id": "{{job.run_id}}"},
        }}],
    }
    job_id = dbx.upsert_job(settings)
    print(f"ingest job {job_id}: {dbx.host}/jobs/{job_id}")
    return 0


def cmd_run_job(dbx, args) -> int:
    n = names(args)
    job = dbx.find_job(n.job)
    if not job:
        raise SystemExit(f"ingest job for ns={n.ns} not found; run deploy-job first")
    run_id = dbx.run_job(int(job["job_id"]))
    print(f"triggered run: {dbx.run_url(run_id)}")
    if args.no_wait:
        return 0
    run = dbx.wait_run(run_id)
    state = run.get("state", {})
    print(f"result: {state.get('result_state')} — {str(state.get('state_message'))[:400]}")
    return 0 if state.get("result_state") == "SUCCESS" else 1


def cmd_status(dbx, args) -> int:
    n = names(args)
    try:
        result = dbx.sql_ok(f"SELECT count(*) AS bronze_rows FROM {n.bronze}")
        bronze = result.dicts()
    except DbxError:
        bronze = "absent"
    print(json.dumps({"bronze": bronze,
                      "objects": len(_walk_files(dbx, n.data_dir)),
                      "commits": len(_walk_files(dbx, n.commit_dir))}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ns", default="demo")
    parser.add_argument("--catalog", default="ow_tp")
    sub = parser.add_subparsers(dest="command", required=True)
    send = sub.add_parser("send-drop")
    send.add_argument("--source", required=True)
    send.add_argument("--strip-suffix", default="")
    pub = sub.add_parser("publish")
    pub.add_argument("--run-id", required=True)
    rec = sub.add_parser("recon-collect")
    rec.add_argument("--out", default="")
    dep = sub.add_parser("deploy-job")
    dep.add_argument("--timeout-seconds", type=int, default=900)
    run = sub.add_parser("run-job")
    run.add_argument("--no-wait", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args()
    dbx = Databricks()
    return {"send-drop": cmd_send_drop, "publish": cmd_publish,
            "recon-collect": cmd_recon_collect, "deploy-job": cmd_deploy_job,
            "run-job": cmd_run_job, "status": cmd_status}[args.command](dbx, args)


if __name__ == "__main__":
    raise SystemExit(main())

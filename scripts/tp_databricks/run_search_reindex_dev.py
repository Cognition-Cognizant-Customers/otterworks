#!/usr/bin/env python3
"""Deploy and run the search reindex pipeline as a throwaway job, then delete it.

The real job (`ow_tp_search_reindex`) is defined as code in
infrastructure/terraform-databricks/jobs_search_reindex.tf and applied by the parent
session, which owns the shared Terraform state. This helper builds the same two-task
serverless pipeline under the throwaway name `ow_tp_dev_search_reindex` so the recon
evidence comes from a real job run of the real notebooks, then removes it again --
nothing is left behind in the shared workspace.

Usage:
    run_search_reindex_dev.py run [ns=demo] [run_date=...] [simulate_source_failure=true] [tasks=publish_index]
    run_search_reindex_dev.py deploy          # upload notebooks only
    run_search_reindex_dev.py teardown        # delete the throwaway job
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVING_TABLE = f"{dbx.CATALOG}.silver.search_index_documents"
NOTEBOOKS = {
    "search_reindex_ingest": REPO_ROOT / "databricks/notebooks/search_reindex_ingest.py",
    "search_reindex_publish": REPO_ROOT / "databricks/notebooks/search_reindex_publish.py",
}
JOB_NAME = f"{dbx.PREFIX}_dev_search_reindex"


def deploy() -> dict[str, str]:
    return {name: dbx.deploy_notebook(str(path), name) for name, path in NOTEBOOKS.items()}


def job_settings(task_keys: tuple[str, ...] | None = None) -> dict:
    """Mirror of the Terraform job definition: serverless notebook tasks, no cluster.

    task_keys narrows the pipeline to a subset of its tasks, used when the landing volume
    is unreachable and bronze was loaded by load_bronze_via_sql.py instead, so only
    publish_index can run. The task definitions themselves are unchanged.
    """
    settings: dict = {
        "name": JOB_NAME,
        "max_concurrent_runs": 1,
        "parameters": [
            {"name": "ns", "default": "demo"},
            {"name": "run_date", "default": ""},
            {"name": "landing_prefix", "default": "search_reindex"},
            {"name": "simulate_source_failure", "default": "false"},
            {"name": "catalog", "default": dbx.CATALOG},
        ],
        "tasks": [
            {
                "task_key": "ingest_bronze",
                "notebook_task": {
                    "notebook_path": f"{dbx.PIPELINE_ROOT}/search_reindex_ingest",
                    "source": "WORKSPACE",
                },
                "timeout_seconds": 1800,
                "max_retries": 2,
                "min_retry_interval_millis": 60000,
            },
            {
                "task_key": "publish_index",
                "depends_on": [{"task_key": "ingest_bronze"}],
                "notebook_task": {
                    "notebook_path": f"{dbx.PIPELINE_ROOT}/search_reindex_publish",
                    "source": "WORKSPACE",
                },
                "timeout_seconds": 1800,
                "max_retries": 1,
                "min_retry_interval_millis": 60000,
            },
        ],
        "tags": {"project": "otterworks-tp", "unit": "search_reindex_weekly", "lifecycle": "throwaway"},
    }
    if task_keys:
        settings["tasks"] = [task for task in settings["tasks"] if task["task_key"] in task_keys]
        if not settings["tasks"]:
            raise SystemExit(f"no pipeline task matches tasks={','.join(task_keys)}")
        for task in settings["tasks"]:
            kept = [dep for dep in task.get("depends_on", []) if dep["task_key"] in task_keys]
            if kept:
                task["depends_on"] = kept
            else:
                task.pop("depends_on", None)
    return settings


def serving_snapshot(ns: str) -> dict[str, int]:
    """Serving row counts per entity type, read immediately after the run finished.

    Recorded in the run artifact so recon can compare a count taken at run time against a
    later live read. Reading both sides at report time would make idempotency hold by
    construction and could never detect drift.
    """
    rows = dbx.sql(
        f"SELECT entity_type, COUNT(*) FROM {SERVING_TABLE} WHERE ns = '{ns}' GROUP BY entity_type"
    )
    return {row[0]: int(row[1]) for row in rows}


def existing_job_id() -> int | None:
    try:
        return dbx.job_id(JOB_NAME)
    except dbx.DatabricksError:
        return None


def ensure_job(task_keys: tuple[str, ...] | None = None) -> int:
    settings = job_settings(task_keys)
    current = existing_job_id()
    if current is None:
        return dbx.request("POST", "/api/2.2/jobs/create", settings)["job_id"]
    dbx.request("POST", "/api/2.2/jobs/reset", {"job_id": current, "new_settings": settings})
    return current


def teardown() -> bool:
    current = existing_job_id()
    if current is None:
        return False
    dbx.request("POST", "/api/2.2/jobs/delete", {"job_id": current})
    return True


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "run"
    params = dict(arg.split("=", 1) for arg in argv[1:])
    if command == "deploy":
        print(json.dumps(deploy(), indent=2))
        return 0
    if command == "teardown":
        print(json.dumps({"deleted": teardown(), "job": JOB_NAME}, indent=2))
        return 0
    if command != "run":
        print(__doc__, file=sys.stderr)
        return 2

    task_keys = tuple(filter(None, params.pop("tasks", "").split(","))) or None
    ns = params.get("ns", "demo")
    if not re.fullmatch(r"[a-z0-9_]+", ns):
        raise SystemExit(f"ns must match [a-z0-9_]+, got {ns!r}")
    deploy()
    ensure_job(task_keys)
    run = dbx.run_job(JOB_NAME, params)
    state = run.get("state", {})
    summary = {
        "job": JOB_NAME,
        "params": params,
        "tasks_selected": list(task_keys) if task_keys else "all",
        "run_id": run.get("run_id"),
        "result_state": state.get("result_state"),
        "state_message": state.get("state_message"),
        "url": run.get("run_page_url"),
        "serving_counts_at_run_end": serving_snapshot(ns),
        "snapshot_taken_at": datetime.now(timezone.utc).isoformat(),
        "tasks": [
            {
                "task_key": task.get("task_key"),
                "result_state": task.get("state", {}).get("result_state"),
                "state_message": task.get("state", {}).get("state_message"),
            }
            for task in run.get("tasks", [])
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0 if state.get("result_state") == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

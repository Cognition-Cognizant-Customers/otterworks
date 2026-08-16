#!/usr/bin/env python3
"""Run the estate orchestration graph as a throwaway multi-task job, then delete it.

The real jobs (`ow_tp_estate_orchestrator`, `ow_tp_estate_rollup`) are defined as code in
infrastructure/terraform-databricks/jobs_estate_rollup.tf and applied by the parent session,
which owns the shared Terraform state. This helper builds the same serverless DAG under the
throwaway name `ow_tp_dev_estate_orchestrator` so the acceptance evidence comes from a real
multi-task job run rather than from prose, then deletes it again unless `keep=true`.

Two documented differences from the Terraform graph, both forced by this workspace:
  * the shipped orchestrator orders units with `run_job_task` edges on each unit's job; those
    jobs are not applied in this workspace (`jobs/list` shows no `ow_tp_*` job), so each unit
    task here is the estate notebook's `unit_gate` stage, which asserts from the data side the
    same thing a `run_job_task` edge asserts from the control side: that unit published its
    slice. The dependency edges, `max_concurrent_runs = 1`, the absence of any cluster and the
    absence of any sleep are identical.
  * the terminal task is the same `databricks/notebooks/estate_rollup.py` the real
    `ow_tp_estate_rollup` job task runs, deployed under a `dev_` name.

`fail_unit=<unit>` is the failure drill required by acceptance check 3: that unit's gate is
pointed at a table the estate does not have, so the upstream task fails for a real reason (a
missing source), the run fails, and the terminal rollup task is never given the chance to
write a green `recon_result`. The drill takes its own `run_date` so the absence of a rollup
row for it is checkable, and the drill's only artifact is the throwaway job, deleted on exit.

Usage:
    run_estate_dev.py run  [ns=demo] [run_date=YYYY-MM-DD] [fail_unit=parse_custbill] [keep=true]
    run_estate_dev.py teardown
"""

from __future__ import annotations

import json
import importlib.util
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbx  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK_SOURCE = REPO_ROOT / "databricks/notebooks/estate_rollup.py"
NOTEBOOK_NAME = f"{dbx.PREFIX}_dev_estate_rollup"
JOB_NAME = f"{dbx.PREFIX}_dev_estate_orchestrator"
MISSING_TABLE_SUFFIX = "_faildrill_missing_source"
RUN_TIMEOUT_S = 5400
TEARDOWN_POLL_S = 2
TEARDOWN_TIMEOUT_S = 600
TERMINAL_STATES = {"TERMINATED", "SKIPPED", "INTERNAL_ERROR"}


class TeardownResult(NamedTuple):
    job: bool
    notebook: bool
    errors: dict[str, str]


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pipeline = _load(NOTEBOOK_SOURCE, "tp_estate_rollup_notebook")

# The Terraform DAG, as (task_key, upstream task keys). Kept in this shape so a divergence
# from jobs_estate_rollup.tf is a visible edit here rather than a silently different run.
UNIT_EDGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sftp_ingest", ()),
    ("parse_custbill", ("sftp_ingest",)),
    ("finance_report", ("parse_custbill",)),
    ("analytics_daily", ()),
    ("user_activity", ("analytics_daily",)),
    ("audit_archive", ()),
    ("search_reindex", ()),
    ("storage_cleanup", ()),
)
LEAF_TASKS = ("finance_report", "user_activity", "audit_archive", "search_reindex", "storage_cleanup")


def deploy() -> str:
    return dbx.deploy_notebook(str(NOTEBOOK_SOURCE), NOTEBOOK_NAME)


def job_settings(ns: str, run_date: str, fail_unit: str | None) -> dict:
    pipeline.validate_ns(ns)
    pipeline.validate_run_date(run_date)
    notebook_path = f"{dbx.PIPELINE_ROOT}/{NOTEBOOK_NAME}"
    tasks: list[dict] = []
    for unit, upstream in UNIT_EDGES:
        base = {
            "ns": ns,
            "catalog": dbx.CATALOG,
            "stage": "unit_gate",
            "unit": unit,
            "run_date": run_date,
        }
        if fail_unit == unit:
            # A real missing relation, not a mocked exception: the gate reads a table the
            # estate does not have, which is precisely what run_all.sh swallowed with
            # `2>/dev/null || true`.
            base["gate_table"] = f"{dbx.CATALOG}.silver.{unit}{MISSING_TABLE_SUFFIX}"
        task: dict = {
            "task_key": unit,
            "notebook_task": {"notebook_path": notebook_path, "source": "WORKSPACE", "base_parameters": base},
            "timeout_seconds": 1800,
            "max_retries": 0,
        }
        if upstream:
            task["depends_on"] = [{"task_key": key} for key in upstream]
        tasks.append(task)

    tasks.append(
        {
            "task_key": "estate_rollup",
            "depends_on": [{"task_key": key} for key in LEAF_TASKS],
            "notebook_task": {
                "notebook_path": notebook_path,
                "source": "WORKSPACE",
                "base_parameters": {
                    "ns": ns,
                    "catalog": dbx.CATALOG,
                    "stage": "rollup",
                    "run_date": run_date,
                    "job_run_id": "{{job.run_id}}",
                },
            },
            "timeout_seconds": 3600,
            "max_retries": 0,
        }
    )
    return {
        "name": JOB_NAME,
        "max_concurrent_runs": 1,
        "tasks": tasks,
        "tags": {"project": "otterworks-tp", "unit": "estate_rollup", "lifecycle": "throwaway"},
    }


def existing_job_id() -> int | None:
    try:
        return dbx.job_id(JOB_NAME)
    except dbx.DatabricksError as exc:
        if exc.status is None and exc.error_code is None:
            return None
        raise


def ensure_job(settings: dict) -> int:
    current = existing_job_id()
    if current is None:
        return dbx.request("POST", "/api/2.2/jobs/create", settings)["job_id"]
    dbx.request("POST", "/api/2.2/jobs/reset", {"job_id": current, "new_settings": settings})
    return current


def teardown() -> TeardownResult:
    job_torn_down = False
    notebook_torn_down = False
    errors: dict[str, str] = {}

    try:
        current = existing_job_id()
        if current is not None:
            runs = dbx.request(
                "GET", f"/api/2.2/jobs/runs/list?job_id={current}&limit=20"
            ).get("runs", [])
            active = [r for r in runs if r.get("state", {}).get("life_cycle_state") not in TERMINAL_STATES]
            for run in active:
                run_id = run.get("run_id")
                dbx.request("POST", "/api/2.2/jobs/runs/cancel", {"run_id": run_id})
                deadline = time.monotonic() + TEARDOWN_TIMEOUT_S
                while True:
                    live = dbx.request("GET", f"/api/2.2/jobs/runs/get?run_id={run_id}")
                    if live.get("state", {}).get("life_cycle_state") in TERMINAL_STATES:
                        break
                    if time.monotonic() >= deadline:
                        raise dbx.DatabricksError(
                            f"run {run_id} did not reach a terminal state before teardown timeout"
                        )
                    time.sleep(TEARDOWN_POLL_S)
            dbx.request("POST", "/api/2.2/jobs/delete", {"job_id": current})
            job_torn_down = True
    except Exception as exc:
        errors["job"] = str(exc)

    notebook_path = f"{dbx.PIPELINE_ROOT}/{NOTEBOOK_NAME}"
    try:
        dbx.request("POST", "/api/2.0/workspace/delete", {"path": notebook_path, "recursive": False})
        notebook_torn_down = True
    except dbx.DatabricksError as exc:
        if exc.status != 404 and exc.error_code != "RESOURCE_DOES_NOT_EXIST":
            errors["notebook"] = str(exc)
    except Exception as exc:
        errors["notebook"] = str(exc)
    return TeardownResult(job_torn_down, notebook_torn_down, errors)


def rollup_rows(ns: str, run_date: str) -> list[dict]:
    """Rollup rows for the drill's own (ns, run_date), read after the run finished."""
    pipeline.validate_ns(ns)
    pipeline.validate_run_date(run_date)
    rows = dbx.sql(
        "SELECT unit, recon_result, job_run_id FROM "
        f"{dbx.CATALOG}.gold.estate_daily_rollup WHERE ns = '{ns}' AND run_date = DATE'{run_date}' ORDER BY unit"
    )
    return [{"unit": r[0], "recon_result": r[1], "job_run_id": r[2]} for r in rows]


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "run"
    params = dict(arg.split("=", 1) for arg in argv[1:])
    if command == "teardown":
        deleted = teardown()
        print(json.dumps({
            "deleted": {
                "job": deleted.job,
                "notebook": deleted.notebook,
                "errors": deleted.errors,
            },
            "job": JOB_NAME,
        }, indent=2))
        return 0
    if command != "run":
        print(__doc__, file=sys.stderr)
        return 2

    ns = params.get("ns", "demo")
    try:
        pipeline.validate_ns(ns)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    run_date = params.get("run_date") or datetime.now(timezone.utc).date().isoformat()
    try:
        pipeline.validate_run_date(run_date)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    fail_unit = params.get("fail_unit") or None
    known = [unit for unit, _ in UNIT_EDGES]
    if fail_unit is not None and fail_unit not in known:
        raise SystemExit(f"fail_unit must be one of {known}, got {fail_unit!r}")
    keep = params.get("keep", "false").lower() == "true"

    deploy()
    summary = None
    try:
        ensure_job(job_settings(ns, run_date, fail_unit))
        run = dbx.run_job(JOB_NAME, None, timeout_s=RUN_TIMEOUT_S)
        state = run.get("state", {})
        summary = {
            "job": JOB_NAME,
            "ns": ns,
            "run_date": run_date,
            "fail_unit": fail_unit,
            "run_id": run.get("run_id"),
            "result_state": state.get("result_state"),
            "state_message": state.get("state_message"),
            "url": run.get("run_page_url"),
            "tasks": [
                {
                    "task_key": task.get("task_key"),
                    "life_cycle_state": task.get("state", {}).get("life_cycle_state"),
                    "result_state": task.get("state", {}).get("result_state"),
                    "state_message": task.get("state", {}).get("state_message"),
                }
                for task in sorted(run.get("tasks", []), key=lambda t: t.get("task_key", ""))
            ],
            "rollup_rows_for_run_date": rollup_rows(ns, run_date),
            "read_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        torn_down = TeardownResult(False, False, {})
        teardown_error = None
        if not keep:
            torn_down = teardown()
            if torn_down.errors:
                teardown_error = "; ".join(
                    f"{resource}: {message}" for resource, message in torn_down.errors.items()
                )
                print(f"warning: failed to tear down {JOB_NAME}: {teardown_error}", file=sys.stderr)
        if summary is not None:
            summary["job_torn_down"] = torn_down.job
            summary["notebook_torn_down"] = torn_down.notebook
            if teardown_error is not None:
                summary["teardown_error"] = str(teardown_error)

    if summary is None:
        raise RuntimeError("run completed without a summary")
    print(json.dumps(summary, indent=2))
    if teardown_error is not None:
        return 1
    return 0 if summary["result_state"] == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

# Databricks notebook source
"""ow_tp_custbill_estate — estate run-log recorder (gold layer).

Final task of the ow_tp_custbill_estate multi-task job, which replaces
etl/legacy-extra/run_all.sh. The legacy script sequenced the CUSTBILL chain
with `sleep 600` between stages and suppressed every failure with
`2>/dev/null || true`; the estate job sequences ingest -> parse -> finance
via real task dependencies, and this task records the per-task outcome of
each estate run into ow_tp.gold.estate_run_log.

It runs with run_if=ALL_DONE so a run that fails upstream still gets an
attributed log row for every task. It receives each task's terminal state
through Databricks dynamic value references ({{tasks.<key>.result_state}}).

Outcome policy (contract malformed_record_policy): a task may never be
recorded as succeeded with a NULL/unknown outcome — an unresolvable task
state fails this task (and therefore the run). After logging, any
non-success upstream state re-raises so the estate run is visibly FAILED
with attribution, never a green run over partial data.

The state-handling core below is pure Python (no Spark imports) so the recon
script can exercise it locally against the deterministic legacy fixture; the
Spark driver runs only when executed as a Databricks notebook.
"""

import re
from datetime import datetime, timezone

CATALOG = "ow_tp"
UNIT = "run_all_orchestration"
RUN_LOG_TABLE = f"{CATALOG}.gold.estate_run_log"

# Task keys of the estate job, in dependency order.
ESTATE_TASKS = ("ingest", "parse", "finance")

# Terminal task result states the Jobs API can report. Anything else is an
# unknown outcome and must fail the run rather than be logged as plausible.
KNOWN_RESULT_STATES = {
    "SUCCESS",
    "SUCCESS_WITH_FAILURES",
    "FAILED",
    "TIMED_OUT",
    "CANCELED",
    "EXCLUDED",
    "UPSTREAM_FAILED",
    "UPSTREAM_CANCELED",
    "MAXIMUM_CONCURRENT_RUNS_REACHED",
}


class UnresolvedTaskStateError(ValueError):
    """A task's outcome is missing, blank, or not a known terminal state."""


class EstateRunFailed(RuntimeError):
    """At least one estate task did not succeed; raised after logging."""


def normalize_state(task_key: str, raw) -> str:
    """Validate one task's reported result state. NULL/blank/unresolved
    template values ('{{...}}') and unknown states raise — the contract
    forbids recording a plausible-looking outcome for an unknown state."""
    if raw is None:
        raise UnresolvedTaskStateError(f"{task_key}: result state is NULL")
    state = str(raw).strip()
    if not state:
        raise UnresolvedTaskStateError(f"{task_key}: result state is blank")
    if "{{" in state or "}}" in state:
        raise UnresolvedTaskStateError(
            f"{task_key}: result state reference did not resolve: {state!r}"
        )
    state = state.upper()
    if state not in KNOWN_RESULT_STATES:
        raise UnresolvedTaskStateError(
            f"{task_key}: unknown result state {state!r}"
        )
    return state


def build_run_log_rows(ns: str, estate_run_id: str, job_id: str, states: dict):
    """Return one validated (ns, estate_run_id, job_id, task_key,
    result_state, succeeded) tuple per estate task, in dependency order.
    Raises UnresolvedTaskStateError before producing any row if any task's
    outcome is unattributable."""
    missing = [k for k in ESTATE_TASKS if k not in states]
    if missing:
        raise UnresolvedTaskStateError(f"missing task outcomes: {missing}")
    normalized = {k: normalize_state(k, states[k]) for k in ESTATE_TASKS}
    return [
        (ns, estate_run_id, job_id, k, normalized[k], normalized[k] == "SUCCESS")
        for k in ESTATE_TASKS
    ]


def failed_tasks(rows) -> list:
    """Task keys whose recorded state is not SUCCESS."""
    return [r[3] for r in rows if not r[5]]


def apply_to_state(state: dict, rows) -> None:
    """Delete-then-insert per (ns, estate_run_id): the idempotent write the
    Spark driver performs. Used by the recon fixture to prove rerun
    idempotency without a live warehouse."""
    log = state.setdefault("estate_run_log", {})
    key = (rows[0][0], rows[0][1])
    log[key] = list(rows)


# COMMAND ----------


def run_recorder(spark, dbutils) -> None:
    from pyspark.sql.types import (
        BooleanType,
        StringType,
        StructField,
        StructType,
        TimestampType,
    )

    dbutils.widgets.text("ns", "demo")
    dbutils.widgets.text("estate_run_id", "")
    dbutils.widgets.text("job_id", "")
    for key in ESTATE_TASKS:
        dbutils.widgets.text(f"{key}_result", "")

    ns = dbutils.widgets.get("ns")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", ns):
        raise ValueError(f"invalid ns parameter: {ns!r}")
    estate_run_id = dbutils.widgets.get("estate_run_id").strip()
    if not estate_run_id or "{{" in estate_run_id:
        raise UnresolvedTaskStateError(
            f"estate_run_id did not resolve: {estate_run_id!r}"
        )
    job_id = dbutils.widgets.get("job_id").strip()

    states = {k: dbutils.widgets.get(f"{k}_result") for k in ESTATE_TASKS}
    rows = build_run_log_rows(ns, estate_run_id, job_id, states)

    spark.sql(
        f"""CREATE TABLE IF NOT EXISTS {RUN_LOG_TABLE} (
            ns STRING NOT NULL,
            estate_run_id STRING NOT NULL,
            job_id STRING,
            task_key STRING NOT NULL,
            result_state STRING NOT NULL,
            succeeded BOOLEAN NOT NULL,
            recorded_at TIMESTAMP
        )"""
    )

    # Idempotent per-run write: delete any rows from a previous attempt of
    # this same estate run, then append. A repaired rerun cannot duplicate.
    spark.sql(
        f"DELETE FROM {RUN_LOG_TABLE} WHERE ns = :ns AND estate_run_id = :rid",
        args={"ns": ns, "rid": estate_run_id},
    )

    schema = StructType(
        [
            StructField("ns", StringType(), False),
            StructField("estate_run_id", StringType(), False),
            StructField("job_id", StringType(), True),
            StructField("task_key", StringType(), False),
            StructField("result_state", StringType(), False),
            StructField("succeeded", BooleanType(), False),
            StructField("recorded_at", TimestampType(), True),
        ]
    )
    now = datetime.now(timezone.utc)
    spark.createDataFrame(
        [(*r, now) for r in rows], schema
    ).write.mode("append").saveAsTable(RUN_LOG_TABLE)

    for r in rows:
        print(f"estate run {estate_run_id} task {r[3]}: {r[4]}")

    bad = failed_tasks(rows)
    if bad:
        raise EstateRunFailed(
            f"estate run {estate_run_id} failed at task(s) {bad}; "
            "downstream tasks did not run on partial data"
        )


if __name__ == "__main__":
    run_recorder(spark, dbutils)  # noqa: F821

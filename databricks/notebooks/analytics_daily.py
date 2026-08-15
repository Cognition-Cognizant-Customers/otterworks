# Databricks notebook source
"""Converted `etl/scripts/analytics_daily.py` — the `ow_tp_analytics_daily` job task.

The legacy cron was one 400-line `main()` that polled a hardcoded production SQS queue,
scanned a DynamoDB table, aggregated in a row-by-row pandas loop, and wrote gzip JSON to
S3 plus an upsert to Postgres. Its headline defect: three consecutive SQS failures ended
the extract (`ERROR: Too many SQS failures, giving up`) and the run continued with zero
events, wrote a zero-everything summary, and exited 0 — silent data loss reported as
success.

What replaces it here:

* the extract is a set-based read of the landing volume (`read_files`), so no credentials
  live in code — the volume and the secret scope `ow_tp` replace `etl/config.ini`;
* every load is a single atomic `INSERT ... REPLACE WHERE ns = ...`, so re-running a
  namespace replaces its slice instead of appending (the legacy script had no idempotency)
  and a failed statement leaves the previous slice intact;
* the aggregate is one `GROUP BY` instead of `df.iterrows()`;
* nothing is dropped silently: every bronze row lands in either `silver.analytics_events`
  or `silver.analytics_events_rejects` with a reason, and the run asserts
  `silver + rejects = bronze`;
* transient failures are retried with a bounded exponential backoff and then **raise**;
  an empty extract raises `ZeroEventExtract`. A run that cannot read its source fails —
  it never reports success on zero events.

The module is deliberately runner-agnostic: `run_pipeline` takes `execute`/`scalar`
callables, so the exact same statement text runs as this notebook task (`spark.sql`) and
through `scripts/tp_databricks/pipeline_analytics_daily.py` on the serverless SQL
warehouse, which is what the recon evidence is produced with.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Iterable

DEFAULT_CATALOG = "ow_tp"
DEFAULT_SOURCE_KIND = "s3"
EVENT_LINE_SCHEMA = "event_id string, event_type string, user_id string, resource_id string, occurred_at string"
# The seeded events carry `occurred_at` as UTC wall clock (`2026-07-29T00:19:51Z`). The
# instant is parsed from the wall-clock part and every date/hour is derived from the same
# value, so summary_date/hour are the event's UTC date and hour regardless of the session
# time zone.
EVENT_TS_FORMAT = "yyyy-MM-dd'T'HH:mm:ss"
REJECT_REASONS = ("missing_event_id", "missing_event_type", "invalid_event_ts", "duplicate_event_id")
# `ns`, `catalog` and the staging table arrive as job parameters / CLI arguments and are
# interpolated into every statement, including `INSERT ... REPLACE WHERE ns = '<ns>'`, which
# deletes the slice it replaces. They are validated as identifiers before any statement is
# built, so a value carrying a quote or a statement terminator cannot widen that predicate.
NS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")


class ZeroEventExtract(RuntimeError):
    """Raised when the extract produced no events.

    This is the legacy defect being retired: `analytics_daily.py` printed
    `WARNING: No events found, exiting` and `sys.exit(0)`. Here it is a failure, so the
    job's failure notification fires instead of a green run with an empty summary.
    """


class ReconcileError(RuntimeError):
    """Raised when silver + rejects does not account for every bronze row."""


def validate_ns(ns: str) -> str:
    if not NS_PATTERN.match(ns or ""):
        raise ValueError(f"invalid ns {ns!r}: expected {NS_PATTERN.pattern}")
    return ns


def validate_identifier(name: str, what: str = "identifier") -> str:
    if not IDENTIFIER_PATTERN.match(name or ""):
        raise ValueError(f"invalid {what} {name!r}: expected {IDENTIFIER_PATTERN.pattern}")
    return name


def _split_sql(text: str) -> list[str]:
    """Split on statement-terminating semicolons only.

    Column comments in the DDL contain semicolons inside quoted literals, and `--` line
    comments can too, so a plain `split(';')` would cut statements in half.
    """
    statements, current, in_string, in_comment = [], [], False, False
    index = 0
    while index < len(text):
        char = text[index]
        if in_comment:
            in_comment = char != "\n"
        elif in_string:
            if char == "'":
                if text[index + 1 : index + 2] == "'":  # escaped quote
                    current.append(char)
                    index += 1
                else:
                    in_string = False
        elif char == "'":
            in_string = True
        elif text[index : index + 2] == "--":
            in_comment = True
            index += 1
            current.append(" ")
            continue
        elif char == ";":
            statements.append("".join(current))
            current = []
            index += 1
            continue
        if not in_comment:
            current.append(char)
        index += 1
    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


def ddl_statements(ddl_text: str, catalog: str = DEFAULT_CATALOG) -> list[dict]:
    """Split databricks/ddl/analytics_daily.sql into executable statements."""
    return [
        {"name": f"ddl_{position}", "sql": body, "retryable": False}
        for position, body in enumerate(_split_sql(ddl_text.replace("${catalog}", catalog)), start=1)
    ]


def _source_path(source_glob: str, ns: str) -> str:
    return source_glob.replace("{ns}", ns)


def _source_relation(source_glob: str, ns: str, catalog: str, source_table: str | None) -> str:
    """The relation the extract reads, one raw source line per row in column `value`.

    Normally the landing volume. `source_table` points the same extract at a staging table
    instead, for environments where the caller cannot write to the volume (the workspace PAT
    needs the `files` scope); everything downstream of this relation is unchanged, so the
    transform under test is identical either way.
    """
    if source_table:
        return f"(SELECT raw_line AS value FROM {source_table} WHERE ns = '{ns}')"
    path = _source_path(source_glob.replace("{catalog}", catalog), ns)
    return f"read_files('{path}', format => 'text', recursiveFileLookup => true)"


def pipeline_statements(
    catalog: str = DEFAULT_CATALOG,
    ns: str = "demo",
    source_glob: str = "/Volumes/{catalog}/bronze/landing/{ns}/analytics_daily/events/",
    source_kind: str = DEFAULT_SOURCE_KIND,
    source_table: str | None = None,
) -> list[dict]:
    """The ordered statement set the job runs, as (name, sql, retryable) records."""
    validate_ns(ns)
    validate_identifier(catalog, "catalog")
    if source_table:
        validate_identifier(source_table, "source table")
    relation = _source_relation(source_glob, ns, catalog, source_table)

    # Read the extract as text, one line per event, so `raw_payload` is the source record
    # verbatim and a record that fails to parse is still recoverable from bronze. The
    # legacy script's `except: pass` on a malformed SQS body left no trace at all.
    bronze = f"""
INSERT INTO {catalog}.bronze.analytics_events_raw REPLACE WHERE ns = '{ns}'
SELECT
  '{ns}'                                                                    AS ns,
  event.event_id                                                            AS event_id,
  event.event_type                                                          AS event_type,
  event.user_id                                                             AS user_id,
  CASE WHEN event.event_type LIKE 'document.%' THEN event.resource_id END    AS document_id,
  CASE WHEN event.event_type LIKE 'file.%' THEN event.resource_id END        AS file_id,
  try_to_timestamp(
    CASE WHEN event.occurred_at RLIKE '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}T[0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}}Z$'
         THEN substr(event.occurred_at, 1, 19) END,
    "{EVENT_TS_FORMAT}"
  )                                                                         AS event_ts,
  '{source_kind}'                                                           AS source,
  raw_payload                                                               AS raw_payload,
  current_timestamp()                                                       AS ingested_at
FROM (
  SELECT
    value                                        AS raw_payload,
    from_json(value, '{EVENT_LINE_SCHEMA}')      AS event
  FROM {relation}
  WHERE trim(value) <> ''
)
""".strip()

    # One classified pass over bronze feeds both silver and the quarantine, so the two are
    # complements by construction: `rn = 1 AND <valid>` goes to silver, everything else is
    # rejected with a reason.
    classified = f"""
SELECT
  ns, event_id, event_type, user_id, document_id, file_id, event_ts, source, raw_payload, ingested_at,
  row_number() OVER (PARTITION BY ns, event_id ORDER BY ingested_at, raw_payload) AS rn
FROM {catalog}.bronze.analytics_events_raw
WHERE ns = '{ns}'
""".strip()
    valid = "event_id IS NOT NULL AND event_id <> '' AND event_type IS NOT NULL AND event_type <> '' AND event_ts IS NOT NULL"

    silver = f"""
INSERT INTO {catalog}.silver.analytics_events REPLACE WHERE ns = '{ns}'
SELECT ns, event_id, event_type, user_id, document_id, file_id, event_ts, source, ingested_at
FROM ({classified})
WHERE rn = 1 AND {valid}
""".strip()

    rejects = f"""
INSERT INTO {catalog}.silver.analytics_events_rejects REPLACE WHERE ns = '{ns}'
SELECT
  ns,
  event_id,
  CASE
    WHEN event_id IS NULL OR event_id = ''     THEN 'missing_event_id'
    WHEN event_type IS NULL OR event_type = '' THEN 'missing_event_type'
    WHEN event_ts IS NULL                      THEN 'invalid_event_ts'
    ELSE 'duplicate_event_id'
  END AS reject_reason,
  source,
  raw_payload,
  current_timestamp() AS rejected_at
FROM ({classified})
WHERE NOT (rn = 1 AND {valid})
""".strip()

    gold = f"""
INSERT INTO {catalog}.gold.analytics_daily_summary REPLACE WHERE ns = '{ns}'
SELECT
  ns,
  date(event_ts)  AS summary_date,
  hour(event_ts)  AS hour,
  user_id,
  document_id,
  file_id,
  event_type,
  count(*)        AS event_count
FROM {catalog}.silver.analytics_events
WHERE ns = '{ns}'
GROUP BY ns, date(event_ts), hour(event_ts), user_id, document_id, file_id, event_type
""".strip()

    return [
        {"name": "bronze_ingest", "sql": bronze, "retryable": True},
        {"name": "silver_events", "sql": silver, "retryable": False},
        {"name": "silver_rejects", "sql": rejects, "retryable": False},
        {"name": "gold_summary", "sql": gold, "retryable": False},
    ]


def count_queries(catalog: str = DEFAULT_CATALOG, ns: str = "demo") -> dict[str, str]:
    """Row counts used by the run's own assertions and by the recon script."""
    validate_ns(ns)
    validate_identifier(catalog, "catalog")
    return {
        "bronze": f"SELECT count(*) FROM {catalog}.bronze.analytics_events_raw WHERE ns = '{ns}'",
        "silver": f"SELECT count(*) FROM {catalog}.silver.analytics_events WHERE ns = '{ns}'",
        "rejects": f"SELECT count(*) FROM {catalog}.silver.analytics_events_rejects WHERE ns = '{ns}'",
        "gold_rows": f"SELECT count(*) FROM {catalog}.gold.analytics_daily_summary WHERE ns = '{ns}'",
        "gold_events": f"SELECT coalesce(sum(event_count), 0) FROM {catalog}.gold.analytics_daily_summary WHERE ns = '{ns}'",
    }


def _execute_with_retry(
    execute: Callable[[str], object],
    statement: dict,
    max_attempts: int,
    backoff_s: float,
    sleep: Callable[[float], None],
    log: Callable[[str], None],
) -> None:
    attempts = max_attempts if statement["retryable"] else 1
    for attempt in range(1, attempts + 1):
        try:
            execute(statement["sql"])
            return
        except Exception as exc:  # noqa: BLE001 - re-raised below; the point is the bounded retry
            if attempt == attempts:
                # The legacy script swallowed this and continued with zero events.
                log(f"{statement['name']}: failed after {attempt} attempt(s): {exc}")
                raise
            wait = backoff_s * (2 ** (attempt - 1))
            log(f"{statement['name']}: attempt {attempt}/{attempts} failed ({exc}); retrying in {wait:.1f}s")
            sleep(wait)


def run_pipeline(
    execute: Callable[[str], object],
    scalar: Callable[[str], object],
    ddl_text: str,
    catalog: str = DEFAULT_CATALOG,
    ns: str = "demo",
    source_glob: str = "/Volumes/{catalog}/bronze/landing/{ns}/analytics_daily/events/",
    source_kind: str = DEFAULT_SOURCE_KIND,
    source_table: str | None = None,
    max_attempts: int = 3,
    backoff_s: float = 2.0,
    apply_ddl: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """Run DDL + the four loads, then assert the run did not lose or duplicate anything."""
    statements: Iterable[dict] = pipeline_statements(catalog, ns, source_glob, source_kind, source_table)
    if apply_ddl:
        validate_identifier(catalog, "catalog")
        for statement in ddl_statements(ddl_text, catalog):
            _execute_with_retry(execute, statement, 1, backoff_s, sleep, log)
        log(f"ddl applied to {catalog}")

    counts: dict[str, int] = {}
    queries = count_queries(catalog, ns)
    for statement in statements:
        _execute_with_retry(execute, statement, max_attempts, backoff_s, sleep, log)
        log(f"{statement['name']}: ok")
        if statement["name"] == "bronze_ingest":
            counts["bronze"] = int(scalar(queries["bronze"]))
            log(f"extracted {counts['bronze']} events into bronze for ns={ns}")
            if counts["bronze"] == 0:
                raise ZeroEventExtract(
                    f"extract produced 0 events for ns={ns} from "
                    f"{source_table or _source_path(source_glob.replace('{catalog}', catalog), ns)}; "
                    "failing the run instead of writing an empty summary"
                )

    for key in ("silver", "rejects", "gold_rows", "gold_events"):
        counts[key] = int(scalar(queries[key]))

    if counts["silver"] + counts["rejects"] != counts["bronze"]:
        raise ReconcileError(
            f"silver ({counts['silver']}) + rejects ({counts['rejects']}) != bronze ({counts['bronze']}) for ns={ns}"
        )
    if counts["gold_events"] != counts["silver"]:
        raise ReconcileError(
            f"gold event_count sum ({counts['gold_events']}) != silver rows ({counts['silver']}) for ns={ns}"
        )
    log(f"run complete for ns={ns}: {counts}")
    return counts


# COMMAND ----------

if __name__ == "__main__":
    dbutils = globals().get("dbutils")
    spark = globals().get("spark")
    if dbutils is None or spark is None:
        raise SystemExit("this notebook runs as the ow_tp_analytics_daily task; use scripts/tp_databricks/pipeline_analytics_daily.py locally")

    dbutils.widgets.text("ns", "demo")
    dbutils.widgets.text("catalog", DEFAULT_CATALOG)
    dbutils.widgets.text("source_glob", "/Volumes/{catalog}/bronze/landing/{ns}/analytics_daily/events/")
    dbutils.widgets.text("source_kind", DEFAULT_SOURCE_KIND)
    dbutils.widgets.text("ddl_path", "/Volumes/{catalog}/bronze/landing/{ns}/analytics_daily/ddl/analytics_daily.sql")
    dbutils.widgets.text("source_table", "")

    job_ns = dbutils.widgets.get("ns")
    job_catalog = dbutils.widgets.get("catalog")
    ddl_file = dbutils.widgets.get("ddl_path").replace("{catalog}", job_catalog).replace("{ns}", job_ns)
    with open(ddl_file, encoding="utf-8") as handle:
        ddl_source = handle.read()

    run_pipeline(
        execute=lambda statement: spark.sql(statement),
        scalar=lambda statement: spark.sql(statement).collect()[0][0],
        ddl_text=ddl_source,
        catalog=job_catalog,
        ns=job_ns,
        source_glob=dbutils.widgets.get("source_glob"),
        source_kind=dbutils.widgets.get("source_kind"),
        source_table=dbutils.widgets.get("source_table") or None,
    )

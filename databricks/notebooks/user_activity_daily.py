# Databricks notebook source
# MAGIC %md
# MAGIC # ow_tp_user_activity — converted from `etl/scripts/user_activity_daily.py`
# MAGIC
# MAGIC Legacy job: daily 05:00 UTC cron. It queried the PostgreSQL table
# MAGIC `analytics_daily_summary` (produced by the 02:00 `analytics_daily.py` cron),
# MAGIC then looped over 30 days of S3 keys **one HTTP GET per day per user file**,
# MAGIC aggregated in pandas, and PUT a JSON report to S3 for admin-service.
# MAGIC
# MAGIC Deficiencies retired here:
# MAGIC
# MAGIC | Legacy | Converted |
# MAGIC |---|---|
# MAGIC | consumes the upstream job's output with no check that it ran (cron ordering by clock) | `stage=freshness_gate` task runs first and refuses the run when the upstream aggregate is missing, empty for the namespace, behind the newest landed event date, or lagging `report_date` by more than `max_upstream_lag_days`; the verdict is persisted as `gold.user_activity_report.upstream_fresh` |
# MAGIC | hardcoded DB password + AWS keys in `/opt/etl/config.ini` | no credentials in this notebook: input is the Unity Catalog landing volume and a UC table, read with the job's identity; any external credential comes from `dbutils.secrets.get(scope="ow_tp", ...)` |
# MAGIC | per-user/per-day S3 reads in a Python loop | one set-based `read_files()` scan of the landed event objects |
# MAGIC | pandas in-memory aggregation | SQL aggregation in bronze → silver → gold |
# MAGIC | `print()` logging, silent `except: pass` | every run appends a verdict row to `gold.user_activity_run_log`; failures raise |
# MAGIC | no retry | job tasks carry `max_retries = 2` (safe because every stage is idempotent) |
# MAGIC | no idempotency | each stage replaces only its own `(ns[, report_date])` partition, so re-running never duplicates rows |
# MAGIC | report PUT to S3 with no verification | gold write is verified: row count and per-user cross-foot against the upstream aggregate are asserted before the run succeeds |
# MAGIC
# MAGIC The SQL below is the single source of truth for the conversion:
# MAGIC `scripts/tp_databricks/recon_user_activity.py` imports this module and executes
# MAGIC the very same statements on the serverless SQL warehouse, so the recon evidence
# MAGIC and the job task can never drift.

# COMMAND ----------

from __future__ import annotations

import json
import os
import re

DEFAULTS = {
    "ns": "demo",
    "report_date": "",  # empty -> the run's UTC date, as the legacy cron used
    "lookback_days": "30",  # legacy hardcoded lookback
    "catalog": "ow_tp",
    # Catalog-relative; qualified with cfg["catalog"] at use, so one setting moves
    # the whole pipeline to another ow_tp-prefixed catalog.
    "upstream_summary_table": "bronze.user_activity_upstream_fixture",
    # Both jobs are daily: more than a day of upstream lag is the failure this
    # conversion exists to remove. Widened only deliberately, e.g. a backfill.
    "max_upstream_lag_days": "1",
    "landing_root": "",  # empty -> /Volumes/<catalog>/bronze/landing
    "ddl_path": "",  # defaults to <landing_root>/<ns>/user_activity/ddl/user_activity_tables.sql
    "stage": "pipeline",  # freshness_gate | pipeline
    # volume: read the landed event objects from the landing volume (production wiring).
    # table: read bronze.user_activity_events_landed, for a workspace token whose
    # scopes exclude `files` and therefore cannot write /Volumes.
    "source_mode": "volume",
    "landed_events_table": "bronze.user_activity_events_landed",
    "on_stale": "fail",  # fail -> raise; mark -> record upstream_fresh=false and write no report
}

# Event-type prefixes; the legacy report grouped per-user counts under the raw
# event type, so the document/file split is derived from the same vocabulary.
DOC_PREFIX = "document."
FILE_PREFIX = "file."


class UpstreamNotFresh(RuntimeError):
    """Raised when the upstream analytics aggregate cannot be trusted for this run."""


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TABLE_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+){1,2}$")
IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
STAGES = ("freshness_gate", "pipeline")
ON_STALE = ("fail", "mark")


def build_config(params: dict[str, str] | None = None) -> dict[str, str]:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (params or {}).items() if v is not None})
    # The catalog reaches USE CATALOG and every qualified table name, so a bare prefix
    # check is not enough: it must also be a single identifier and nothing else.
    if not IDENT_RE.match(str(cfg["catalog"])) or not str(cfg["catalog"]).startswith("ow_tp"):
        raise ValueError(f"catalog must be an ow_tp-prefixed identifier in this shared "
                         f"workspace: {cfg['catalog']!r}")
    ns = str(cfg["ns"])
    if not ns.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"unsafe ns {ns!r}")
    cfg["lookback_days"] = str(int(cfg["lookback_days"]))
    cfg["max_upstream_lag_days"] = str(int(cfg["max_upstream_lag_days"]))
    if not cfg["landing_root"]:
        cfg["landing_root"] = f"/Volumes/{cfg['catalog']}/bronze/landing"
    # Every parameter below is interpolated into SQL, so each is constrained to a shape
    # that cannot carry a quote or a second statement out of a job/widget parameter.
    for key in ("upstream_summary_table", "landed_events_table"):
        if not TABLE_RE.match(str(cfg[key])):
            raise ValueError(f"{key} must be schema.table or catalog.schema.table: {cfg[key]!r}")
        if cfg[key].count(".") == 1:
            cfg[key] = f"{cfg['catalog']}.{cfg[key]}"
    if cfg["report_date"] and not DATE_RE.match(str(cfg["report_date"])):
        raise ValueError(f"report_date must be YYYY-MM-DD: {cfg['report_date']!r}")
    if cfg["source_mode"] not in ("volume", "table"):
        raise ValueError(f"source_mode must be volume or table: {cfg['source_mode']!r}")
    # stage is interpolated into the run-log INSERT and on_stale drives the refusal path;
    # both come from job/widget parameters, so both are closed sets.
    if cfg["stage"] not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}: {cfg['stage']!r}")
    if cfg["on_stale"] not in ON_STALE:
        raise ValueError(f"on_stale must be one of {ON_STALE}: {cfg['on_stale']!r}")
    cfg["unit_root"] = f"{cfg['landing_root']}/{ns}/user_activity"
    cfg["events_root"] = f"{cfg['unit_root']}/events"
    if not cfg["ddl_path"]:
        cfg["ddl_path"] = f"{cfg['unit_root']}/ddl/user_activity_tables.sql"
    # SQL expression for the report date: the parameter, or the run's UTC date.
    cfg["report_date_expr"] = (
        f"DATE'{cfg['report_date']}'" if cfg["report_date"] else "CURRENT_DATE()"
    )
    return cfg


def ddl_statements(ddl_sql: str) -> list[str]:
    """Split a committed DDL file into executable statements.

    Comment lines are dropped first, then statements are split on semicolons that
    sit outside a string literal — column COMMENT text contains semicolons.
    """
    body = "\n".join(
        line for line in ddl_sql.splitlines() if not line.strip().startswith("--")
    )
    statements, current, quote = [], [], ""
    for char in body:
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == ";":
            statements.append("".join(current))
            current = []
            continue
        current.append(char)
    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


def events_source(cfg: dict[str, str]) -> str:
    """One set-based read of the landed events, replacing the per-user/per-day S3 loop.

    Returned as an inline subquery rather than a temporary view: the recon script
    executes the identical statements over the SQL Statement Execution API, where
    each statement is its own session and a temp view would not survive.
    """
    if cfg["source_mode"] == "volume":
        source = f"""read_files(
  '{cfg["events_root"]}',
  format => 'json',
  recursiveFileLookup => true,
  schema => 'event_id STRING, event_type STRING, occurred_at STRING, resource_id STRING, user_id STRING'
)"""
        predicate = ""
    else:
        source = cfg["landed_events_table"]
        predicate = f"AND ns = '{cfg['ns']}'"
    return f"""(
SELECT
  '{cfg["ns"]}'                                  AS ns,
  event_id,
  event_type,
  user_id,
  resource_id,
  CAST(occurred_at AS TIMESTAMP)                 AS occurred_at,
  CAST(occurred_at AS DATE)                      AS activity_date,
  DATE_FORMAT(CAST(occurred_at AS TIMESTAMP), 'HH') AS hour_bucket
FROM {source}
WHERE user_id IS NOT NULL AND occurred_at IS NOT NULL {predicate}
)"""


def upstream_source(cfg: dict[str, str]) -> str:
    """Upstream analytics aggregate, restricted to this namespace and the legacy window.

    The legacy daily-summary query was `WHERE report_date BETWEEN ds - 30 days AND ds`.
    """
    return f"""(
SELECT *
FROM {cfg["upstream_summary_table"]}
WHERE ns = '{cfg["ns"]}'
  AND report_date BETWEEN {cfg["report_date_expr"]} - INTERVAL {cfg["lookback_days"]} DAYS
                      AND {cfg["report_date_expr"]}
)"""


def freshness_probe(cfg: dict[str, str]) -> str:
    """The freshness facts the legacy cron never established."""
    events, upstream = events_source(cfg), upstream_source(cfg)
    return f"""
SELECT
  (SELECT MAX(report_date) FROM {upstream} u)                       AS upstream_summary_date,
  (SELECT COUNT(*) FROM {upstream} u)                               AS upstream_rows,
  -- Bounded by the report date: the upstream side is windowed to report_date, so
  -- comparing it against events landed *after* that date would refuse every backfill
  -- as stale.
  (SELECT MAX(activity_date) FROM {events} e
    WHERE e.activity_date <= {cfg["report_date_expr"]})             AS latest_event_date,
  DATEDIFF({cfg["report_date_expr"]}, (SELECT MAX(report_date) FROM {upstream} u)) AS upstream_lag_days,
  {cfg["report_date_expr"]}                                        AS report_date
"""


def bronze_statements(cfg: dict[str, str]) -> list[str]:
    """Reload this namespace's bronze slice: per-user event metrics + the upstream aggregate."""
    catalog, ns = cfg["catalog"], cfg["ns"]
    events, upstream = events_source(cfg), upstream_source(cfg)
    return [
        f"DELETE FROM {catalog}.bronze.user_activity_raw WHERE ns = '{ns}'",
        f"""
INSERT INTO {catalog}.bronze.user_activity_raw
SELECT ns, user_id, activity_date, event_type AS metric, CAST(COUNT(*) AS DOUBLE) AS value,
       'events_by_type' AS source, CURRENT_TIMESTAMP() AS ingested_at
FROM {events} ua_events
GROUP BY ns, user_id, activity_date, event_type
""",
        f"""
INSERT INTO {catalog}.bronze.user_activity_raw
SELECT ns, user_id, activity_date, CONCAT('hour.', hour_bucket) AS metric,
       CAST(COUNT(*) AS DOUBLE) AS value,
       'events_by_hour' AS source, CURRENT_TIMESTAMP() AS ingested_at
FROM {events} ua_events
GROUP BY ns, user_id, activity_date, hour_bucket
""",
        f"""
INSERT INTO {catalog}.bronze.user_activity_raw
SELECT ns, user_id, activity_date, 'last_event_epoch' AS metric,
       CAST(UNIX_TIMESTAMP(MAX(occurred_at)) AS DOUBLE) AS value,
       'events_last_ts' AS source, CURRENT_TIMESTAMP() AS ingested_at
FROM {events} ua_events
GROUP BY ns, user_id, activity_date
""",
        # The upstream aggregate lands beside the per-user rows (user_id '*') so the
        # gold cross-foot can be reproduced from bronze alone.
        f"""
INSERT INTO {catalog}.bronze.user_activity_raw
SELECT '{ns}' AS ns, '*' AS user_id, report_date AS activity_date, metric,
       CAST(value AS DOUBLE) AS value,
       'analytics_summary' AS source, CURRENT_TIMESTAMP() AS ingested_at
FROM {upstream} ua_upstream
LATERAL VIEW STACK(6,
  'total_events', total_events, 'active_users', active_users,
  'documents_created', documents_created, 'documents_edited', documents_edited,
  'files_uploaded', files_uploaded, 'files_deleted', files_deleted
) s AS metric, value
""",
    ]


def silver_statements(cfg: dict[str, str]) -> list[str]:
    catalog, ns = cfg["catalog"], cfg["ns"]
    return [
        f"DELETE FROM {catalog}.silver.user_activity_daily WHERE ns = '{ns}'",
        f"""
INSERT INTO {catalog}.silver.user_activity_daily
SELECT p.ns, p.user_id, p.activity_date,
       CAST(p.documents_touched AS BIGINT),
       CAST(p.files_touched AS BIGINT),
       CAST(p.events - p.documents_touched - p.files_touched AS BIGINT) AS other_events,
       CAST(p.events AS BIGINT),
       CAST(h.active_hours AS INT),
       t.last_active_ts,
       CURRENT_TIMESTAMP() AS updated_at
FROM (
  SELECT ns, user_id, activity_date,
         SUM(CASE WHEN metric LIKE '{DOC_PREFIX}%'  THEN value ELSE 0 END) AS documents_touched,
         SUM(CASE WHEN metric LIKE '{FILE_PREFIX}%' THEN value ELSE 0 END) AS files_touched,
         SUM(value)                                                        AS events
  FROM {catalog}.bronze.user_activity_raw
  WHERE ns = '{ns}' AND source = 'events_by_type'
  GROUP BY ns, user_id, activity_date
) p
LEFT JOIN (
  SELECT ns, user_id, activity_date, COUNT(DISTINCT metric) AS active_hours
  FROM {catalog}.bronze.user_activity_raw
  WHERE ns = '{ns}' AND source = 'events_by_hour' AND value > 0
  GROUP BY ns, user_id, activity_date
) h ON h.ns = p.ns AND h.user_id = p.user_id AND h.activity_date = p.activity_date
LEFT JOIN (
  SELECT ns, user_id, activity_date,
         CAST(FROM_UNIXTIME(MAX(value)) AS TIMESTAMP) AS last_active_ts
  FROM {catalog}.bronze.user_activity_raw
  WHERE ns = '{ns}' AND source = 'events_last_ts'
  GROUP BY ns, user_id, activity_date
) t ON t.ns = p.ns AND t.user_id = p.user_id AND t.activity_date = p.activity_date
""",
    ]


def gold_statements(cfg: dict[str, str], upstream_summary_date: str, upstream_fresh: bool) -> list[str]:
    """Roll silver up over the legacy lookback window into the report.

    The legacy per-user window is the S3 read loop's `range(lookback_days)` — the
    report date and the 29 days before it — while the daily-summary SQL used
    `BETWEEN ds - 30 days AND ds`. Both windows are reproduced exactly.
    """
    catalog, ns = cfg["catalog"], cfg["ns"]
    window_start = f"{cfg['report_date_expr']} - INTERVAL {int(cfg['lookback_days']) - 1} DAYS"
    fresh_sql = "TRUE" if upstream_fresh else "FALSE"
    upstream_date_sql = f"DATE'{upstream_summary_date}'" if upstream_summary_date else "CAST(NULL AS DATE)"
    return [
        f"""
DELETE FROM {catalog}.gold.user_activity_report
WHERE ns = '{ns}' AND report_date = {cfg["report_date_expr"]}
""",
        f"""
INSERT INTO {catalog}.gold.user_activity_report
SELECT ns,
       {cfg["report_date_expr"]}         AS report_date,
       user_id,
       CAST(SUM(documents_touched) AS BIGINT) AS documents_touched,
       CAST(SUM(files_touched) AS BIGINT)     AS files_touched,
       CAST(SUM(events) AS BIGINT)            AS events,
       MAX(last_active_ts)                    AS last_active_ts,
       CAST(COUNT(DISTINCT activity_date) AS INT) AS active_days,
       {upstream_date_sql}                    AS upstream_summary_date,
       {fresh_sql}                            AS upstream_fresh,
       CURRENT_TIMESTAMP()                    AS generated_at
FROM {catalog}.silver.user_activity_daily
WHERE ns = '{ns}'
  AND activity_date BETWEEN {window_start} AND {cfg["report_date_expr"]}
GROUP BY ns, user_id
""",
    ]


def verification_probe(cfg: dict[str, str]) -> str:
    """Post-write verification: the legacy job PUT its report and checked nothing."""
    catalog, ns = cfg["catalog"], cfg["ns"]
    return f"""
SELECT
  (SELECT COUNT(*) FROM {catalog}.gold.user_activity_report
    WHERE ns = '{ns}' AND report_date = {cfg["report_date_expr"]})                        AS gold_rows,
  (SELECT COUNT(DISTINCT user_id) FROM {catalog}.gold.user_activity_report
    WHERE ns = '{ns}' AND report_date = {cfg["report_date_expr"]})                        AS gold_users,
  (SELECT COALESCE(SUM(events), 0) FROM {catalog}.gold.user_activity_report
    WHERE ns = '{ns}' AND report_date = {cfg["report_date_expr"]})                        AS gold_events,
  (SELECT COALESCE(SUM(value), 0) FROM {catalog}.bronze.user_activity_raw
    WHERE ns = '{ns}' AND source = 'analytics_summary' AND metric = 'total_events')       AS upstream_total_events
"""


def run_log_statement(cfg: dict[str, str], stage: str, upstream_summary_date: str | None,
                      upstream_fresh: bool, status: str, detail: str, rows_written: int) -> str:
    catalog, ns = cfg["catalog"], cfg["ns"]
    upstream_date_sql = f"DATE'{upstream_summary_date}'" if upstream_summary_date else "CAST(NULL AS DATE)"
    safe_detail = detail.replace("'", "''")
    return f"""
INSERT INTO {catalog}.gold.user_activity_run_log
SELECT '{ns}', CURRENT_TIMESTAMP(), {cfg["report_date_expr"]}, '{stage}',
       {upstream_date_sql}, {str(upstream_fresh).upper()}, '{status}', '{safe_detail}',
       CAST({rows_written} AS BIGINT)
"""


# COMMAND ----------


def evaluate_freshness(cfg: dict[str, str], probe: dict) -> tuple[bool, str, str]:
    """Return (fresh, status, detail) from the freshness probe row."""
    upstream_rows = int(probe["upstream_rows"] or 0)
    upstream_date = probe["upstream_summary_date"]
    latest_event_date = probe["latest_event_date"]
    lag = probe["upstream_lag_days"]
    if upstream_rows == 0 or upstream_date is None:
        return False, "refused_missing_upstream", (
            f"upstream {cfg['upstream_summary_table']} has no rows for ns={cfg['ns']} "
            f"inside the {cfg['lookback_days']}-day window ending {probe['report_date']}: "
            "the analytics job has not run for this window"
        )
    if latest_event_date is not None and str(upstream_date) < str(latest_event_date):
        return False, "refused_stale_upstream", (
            f"upstream latest report_date {upstream_date} is behind the newest landed event date "
            f"{latest_event_date}: the analytics job is late or failed for that date"
        )
    if lag is not None and int(lag) > int(cfg["max_upstream_lag_days"]):
        return False, "refused_stale_upstream", (
            f"upstream latest report_date {upstream_date} lags report_date {probe['report_date']} "
            f"by {lag} days (limit {cfg['max_upstream_lag_days']})"
        )
    return True, "ok", (
        f"upstream {cfg['upstream_summary_table']} covers {upstream_date} "
        f"({upstream_rows} rows in window, latest event date {latest_event_date})"
    )


class SparkRunner:
    """Statement runner backed by a Spark session (the job task's runtime).

    The recon script supplies an equivalent runner backed by the serverless SQL
    warehouse, so both execute the identical statement text.
    """

    def __init__(self, spark):
        self.spark = spark

    def execute(self, statement: str) -> None:
        self.spark.sql(statement)

    def row(self, statement: str) -> dict:
        return self.spark.sql(statement).collect()[0].asDict()

    def read_text(self, path: str) -> str:
        if path.startswith("/Volumes/") and os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        return "".join(row[0] + "\n" for row in self.spark.read.text(path).collect())


def main(runner, params: dict[str, str], ddl_sql: str | None = None) -> dict:
    cfg = build_config(params)
    stage = cfg["stage"]
    print(f"[user_activity] stage={stage} ns={cfg['ns']} report_date={cfg['report_date'] or 'CURRENT_DATE'}")

    # The DDL is catalog-relative; the caller's catalog decides where it lands.
    runner.execute(f"USE CATALOG {cfg['catalog']}")
    for statement in ddl_statements(ddl_sql if ddl_sql is not None else runner.read_text(cfg["ddl_path"])):
        runner.execute(statement)

    probe = runner.row(freshness_probe(cfg))
    fresh, status, detail = evaluate_freshness(cfg, probe)
    upstream_date = None if probe["upstream_summary_date"] is None else str(probe["upstream_summary_date"])
    print(f"[user_activity] freshness: fresh={fresh} status={status} :: {detail}")

    if not fresh:
        runner.execute(run_log_statement(cfg, stage, upstream_date, False, status, detail, 0))
        if cfg["on_stale"] == "fail":
            raise UpstreamNotFresh(detail)
        # on_stale=mark: record the verdict, produce no report over stale data.
        return {"stage": stage, "upstream_fresh": False, "status": status, "detail": detail, "rows_written": 0}

    if stage == "freshness_gate":
        runner.execute(run_log_statement(cfg, stage, upstream_date, True, status, detail, 0))
        return {"stage": stage, "upstream_fresh": True, "status": status, "detail": detail, "rows_written": 0}

    for statement in bronze_statements(cfg) + silver_statements(cfg):
        runner.execute(statement)
    for statement in gold_statements(cfg, upstream_date, True):
        runner.execute(statement)

    verified = runner.row(verification_probe(cfg))
    if int(verified["gold_rows"]) == 0:
        raise RuntimeError("gold write produced no rows: refusing to report success")
    if int(verified["gold_rows"]) != int(verified["gold_users"]):
        raise RuntimeError(
            f"gold is not one row per user: {verified['gold_rows']} rows for {verified['gold_users']} users"
        )
    if int(verified["gold_events"]) > int(verified["upstream_total_events"]):
        raise RuntimeError(
            f"gold events {verified['gold_events']} exceed upstream total_events "
            f"{verified['upstream_total_events']}: report cannot invent activity"
        )
    detail = f"{detail}; verified {json.dumps(verified, default=str)}"
    runner.execute(run_log_statement(cfg, stage, upstream_date, True, "ok", detail, int(verified["gold_rows"])))
    print(f"[user_activity] wrote {verified['gold_rows']} report rows ({verified['gold_events']} events)")
    return {"stage": stage, "upstream_fresh": True, "status": "ok", "detail": detail, **verified}


def _widget_params(dbutils_ref) -> dict[str, str]:
    params = {}
    for key in DEFAULTS:
        dbutils_ref.widgets.text(key, DEFAULTS[key])
        params[key] = dbutils_ref.widgets.get(key)
    return params


# Databricks executes the whole source file; guarded so the module can also be
# imported locally (by the recon script) without a Spark session.
if "dbutils" in globals():  # pragma: no cover - Databricks runtime only
    result = main(SparkRunner(spark), _widget_params(dbutils))  # noqa: F821 - provided by the runtime
    dbutils.notebook.exit(json.dumps(result, default=str))  # noqa: F821

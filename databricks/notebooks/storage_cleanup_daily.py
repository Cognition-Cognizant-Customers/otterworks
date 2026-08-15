# Databricks notebook source
"""ow_tp_storage_cleanup -- orphaned-object detection, converted from the 2014 cron.

The legacy `etl/scripts/storage_cleanup_daily.py` listed one S3 prefix, probed
DynamoDB once per object, and moved anything it could not find a metadata item
for into quarantine -- in the same pass, unconditionally. A metadata read that
failed halfway therefore looked exactly like "these files have no owner", so a
transient DynamoDB problem deleted live customer files.

This module is both the job's notebook and the single source of the SQL the
recon executes: `ddl_statements()` and `pipeline_statements()` are plain
functions, so the local driver imports this file and runs byte-identical SQL
through the serverless warehouse instead of maintaining a second copy.

Safety model, in the SQL rather than in a comment:
  * the extract records `metadata_read_complete` in
    `ow_tp.bronze.storage_extract_manifest`, and the pipeline recomputes
    `metadata_read_ok` from it plus a loaded-row cross-check;
  * when `metadata_read_ok` is false every object still lands in
    `silver.storage_orphans`, but as `candidate_unverified_metadata_read` --
    never as a confirmed orphan, and never counted as quarantinable;
  * `quarantined_count` is the number of objects a run *authorises* for
    quarantine. It is 0 whenever the run is a dry run (the default) or the
    metadata read was incomplete, so an incomplete read quarantines nothing.

Every statement is scoped to (`ns`, `scenario`) and deletes its own slice before
writing it, so re-running is idempotent instead of additive.
"""

import re

CATALOG = "ow_tp"

# Parameters reach the SQL as literals, so they are constrained rather than
# escaped: anything that is not a plain namespace/scenario token or an ISO date
# is rejected before a statement is built.
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DDL_SQL = """
CREATE TABLE IF NOT EXISTS {catalog}.bronze.storage_objects_raw (
  ns            STRING,
  bucket        STRING,
  key           STRING,
  size_bytes    BIGINT,
  last_modified TIMESTAMP,
  listed_at     TIMESTAMP
)
-- @statement
CREATE TABLE IF NOT EXISTS {catalog}.bronze.file_metadata_raw (
  ns          STRING,
  file_id     STRING,
  storage_key STRING,
  owner_id    STRING,
  size_bytes  BIGINT,
  created_at  TIMESTAMP
)
-- @statement
CREATE TABLE IF NOT EXISTS {catalog}.bronze.storage_extract_manifest (
  ns                     STRING,
  scenario               STRING,
  source_bucket          STRING,
  source_table           STRING,
  objects_expected       BIGINT,
  objects_bytes          BIGINT,
  metadata_expected      BIGINT,
  metadata_read_complete BOOLEAN,
  extracted_at           TIMESTAMP,
  loaded_at              TIMESTAMP
)
-- @statement
CREATE TABLE IF NOT EXISTS {catalog}.silver.storage_orphans (
  ns               STRING,
  bucket           STRING,
  key              STRING,
  size_bytes       BIGINT,
  orphan_reason    STRING,
  detected_at      TIMESTAMP,
  metadata_read_ok BOOLEAN,
  scenario         STRING
)
-- @statement
CREATE TABLE IF NOT EXISTS {catalog}.gold.storage_cleanup_savings (
  ns                STRING,
  run_date          DATE,
  objects_scanned   BIGINT,
  metadata_rows     BIGINT,
  orphan_count      BIGINT,
  orphan_bytes      BIGINT,
  quarantined_count BIGINT,
  dry_run           BOOLEAN,
  scenario          STRING,
  metadata_read_ok  BOOLEAN,
  generated_at      TIMESTAMP
)
"""

# The one expression the whole safety story rests on. A read counts as complete
# only if the extract said it finished AND every row it claims to have read is
# actually in bronze AND it read something at all -- an empty bronze slice is a
# failed read, not a bucket full of orphans.
_GUARD_CTE = """
guard AS (
  SELECT COALESCE(m.metadata_read_complete
                  AND l.metadata_rows = m.metadata_expected
                  AND l.metadata_rows > 0, false) AS metadata_read_ok
  FROM (
    -- Aggregates, so this is exactly one row whatever the manifest holds: two
    -- rows would fan the orphan set out, zero rows would make the whole
    -- pipeline silently write nothing. No manifest at all means no verified
    -- read, which COALESCE turns into metadata_read_ok = false.
    SELECT max_by(metadata_read_complete, loaded_at) AS metadata_read_complete,
           max_by(metadata_expected, loaded_at)      AS metadata_expected
    FROM {catalog}.bronze.storage_extract_manifest
    WHERE ns = '{ns}'
  ) m
  CROSS JOIN (
    SELECT COUNT(*) AS metadata_rows
    FROM {catalog}.bronze.file_metadata_raw
    WHERE ns = '{ns}'
  ) l
)
"""

PIPELINE_SQL = """
DELETE FROM {catalog}.silver.storage_orphans
WHERE ns = '{ns}' AND scenario = '{scenario}'
-- @statement
INSERT INTO {catalog}.silver.storage_orphans
WITH {guard_cte}
SELECT
  o.ns,
  o.bucket,
  o.key,
  o.size_bytes,
  CASE WHEN g.metadata_read_ok THEN 'no_metadata_row'
       ELSE 'candidate_unverified_metadata_read' END AS orphan_reason,
  current_timestamp() AS detected_at,
  g.metadata_read_ok,
  '{scenario}' AS scenario
FROM {catalog}.bronze.storage_objects_raw o
CROSS JOIN guard g
WHERE o.ns = '{ns}'
  AND NOT EXISTS (
    SELECT 1 FROM {catalog}.bronze.file_metadata_raw m
    WHERE m.ns = o.ns AND m.storage_key = o.key
  )
-- @statement
DELETE FROM {catalog}.gold.storage_cleanup_savings
WHERE ns = '{ns}' AND scenario = '{scenario}' AND run_date = DATE '{run_date}'
-- @statement
INSERT INTO {catalog}.gold.storage_cleanup_savings
WITH {guard_cte},
orphans AS (
  SELECT
    COUNT_IF(metadata_read_ok) AS orphan_count,
    COALESCE(SUM(CASE WHEN metadata_read_ok THEN size_bytes ELSE 0 END), 0) AS orphan_bytes
  FROM {catalog}.silver.storage_orphans
  WHERE ns = '{ns}' AND scenario = '{scenario}'
)
SELECT
  '{ns}' AS ns,
  DATE '{run_date}' AS run_date,
  (SELECT COUNT(*) FROM {catalog}.bronze.storage_objects_raw WHERE ns = '{ns}') AS objects_scanned,
  (SELECT COUNT(*) FROM {catalog}.bronze.file_metadata_raw WHERE ns = '{ns}') AS metadata_rows,
  o.orphan_count,
  o.orphan_bytes,
  CASE WHEN {dry_run_sql} THEN 0 ELSE o.orphan_count END AS quarantined_count,
  {dry_run_sql} AS dry_run,
  '{scenario}' AS scenario,
  g.metadata_read_ok,
  current_timestamp() AS generated_at
FROM orphans o
CROSS JOIN guard g
"""


def _split(sql: str) -> list:
    return [part.strip() for part in sql.split("-- @statement") if part.strip()]


def _checked(label: str, value: str, pattern=_TOKEN) -> str:
    if not pattern.match(value or ""):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def ddl_statements(catalog: str = CATALOG) -> list:
    """Idempotent CREATE TABLE statements for this unit's tables."""
    catalog = _checked("catalog", catalog)
    return _split(DDL_SQL.format(catalog=catalog))


def pipeline_statements(
    ns: str,
    run_date: str,
    dry_run: bool = True,
    scenario: str = "nominal",
    catalog: str = CATALOG,
) -> list:
    """The set-based orphan detection + savings report, for one namespace slice."""
    ns = _checked("ns", ns)
    scenario = _checked("scenario", scenario)
    catalog = _checked("catalog", catalog)
    run_date = _checked("run_date", run_date, _ISO_DATE)
    guard_cte = _GUARD_CTE.format(catalog=catalog, ns=ns).strip().rstrip(",")
    sql = PIPELINE_SQL.format(
        catalog=catalog,
        ns=ns,
        run_date=run_date,
        scenario=scenario,
        dry_run_sql="true" if dry_run else "false",
        guard_cte=guard_cte,
    )
    return _split(sql)


def _in_databricks() -> bool:
    try:
        dbutils  # noqa: F821
    except NameError:
        return False
    return True


if _in_databricks():  # pragma: no cover -- exercised by the job, not locally
    import datetime

    for _name, _default in (
        ("stage", "pipeline"),
        ("ns", "demo"),
        ("dry_run", "true"),
        ("scenario", "nominal"),
        ("run_date", ""),
        ("catalog", CATALOG),
    ):
        dbutils.widgets.text(_name, _default)  # noqa: F821

    stage = dbutils.widgets.get("stage")  # noqa: F821
    catalog = _checked("catalog", dbutils.widgets.get("catalog") or CATALOG)  # noqa: F821
    ns = _checked("ns", dbutils.widgets.get("ns"))  # noqa: F821
    scenario = _checked("scenario", dbutils.widgets.get("scenario") or "nominal")  # noqa: F821
    dry_run = dbutils.widgets.get("dry_run").strip().lower() != "false"  # noqa: F821
    run_date = _checked(  # noqa: F821
        "run_date",
        dbutils.widgets.get("run_date") or datetime.date.today().isoformat(),  # noqa: F821
        _ISO_DATE,
    )

    statements = (
        ddl_statements(catalog=catalog)
        if stage == "ddl"
        else pipeline_statements(
            ns=ns, run_date=run_date, dry_run=dry_run, scenario=scenario, catalog=catalog
        )
    )
    for statement in statements:
        print(statement.splitlines()[0][:110])
        spark.sql(statement)  # noqa: F821

    if stage != "ddl":
        report = spark.sql(  # noqa: F821
            f"""
            SELECT objects_scanned, metadata_rows, orphan_count, orphan_bytes,
                   quarantined_count, dry_run, metadata_read_ok
            FROM {catalog}.gold.storage_cleanup_savings
            WHERE ns = '{ns}' AND scenario = '{scenario}' AND run_date = DATE '{run_date}'
            """
        ).collect()[0]
        print(report)
        # Fail the task -- and so trigger the job's failure alert -- when the
        # metadata side could not be trusted. The legacy script logged nothing
        # and quarantined anyway; here the run is loud and quarantines nothing.
        if not report["metadata_read_ok"]:
            raise RuntimeError(
                f"metadata read incomplete for ns={ns} scenario={scenario}: "
                f"{report['orphan_count']} orphans confirmed, "
                f"{report['quarantined_count']} objects quarantined -- "
                "candidates recorded in silver.storage_orphans for review"
            )

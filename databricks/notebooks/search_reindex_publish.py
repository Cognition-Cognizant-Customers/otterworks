# Databricks notebook source
# MAGIC %md
# MAGIC # search_reindex_publish
# MAGIC
# MAGIC Task 2 of `ow_tp_search_reindex` (converted from `etl/scripts/search_reindex_weekly.py`).
# MAGIC
# MAGIC Build-then-swap, the whole point of the conversion:
# MAGIC
# MAGIC 1. project bronze into the typed, deduplicated staging index;
# MAGIC 2. reconcile staged rows against the bronze source counts, and refuse an empty build;
# MAGIC 3. only then replace this namespace's rows in the serving table
# MAGIC    `ow_tp.silver.search_index_documents`;
# MAGIC 4. record the reconciliation in `ow_tp.gold.search_reindex_summary`.
# MAGIC
# MAGIC A count divergence fails the run *and* leaves an auditable gold row with
# MAGIC `counts_match = false, swap_completed = false`; the legacy cron printed the mismatch and
# MAGIC left the index in whatever half-built state it had reached.

# COMMAND ----------

import json
import re
from datetime import date, datetime, timezone

CATALOG = "ow_tp"
BRONZE_TABLE = f"{CATALOG}.bronze.search_documents_raw"
STAGING_TABLE = f"{CATALOG}.silver.search_index_documents_staging"
SERVING_TABLE = f"{CATALOG}.silver.search_index_documents"
SUMMARY_TABLE = f"{CATALOG}.gold.search_reindex_summary"

INDEX_COLUMNS = [
    "ns", "entity_type", "entity_id", "title", "content", "name", "mime_type",
    "folder_id", "size_bytes", "owner_id", "tags", "created_at", "updated_at",
    "run_date", "indexed_at",
]

dbutils.widgets.text("ns", "demo")
dbutils.widgets.text("run_date", "")

ns = dbutils.widgets.get("ns").strip()
run_date = dbutils.widgets.get("run_date").strip() or date.today().isoformat()

if not re.fullmatch(r"[a-z0-9_]+", ns):
    raise ValueError(f"ns must match [a-z0-9_]+, got {ns!r}")
if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_date):
    raise ValueError(f"run_date must be YYYY-MM-DD, got {run_date!r}")

indexed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat(sep=" ")


def log(event, **fields):
    print(json.dumps({"logger": "search_reindex.publish", "event": event, "ns": ns, "run_date": run_date, **fields}))


def counts_by_entity(sql_text):
    return {row["entity_type"]: int(row["n"]) for row in spark.sql(sql_text).collect()}


def write_summary(source_counts, indexed_counts, swap_completed):
    """Record one gold row per entity type. Rewritten per (ns, run_date), so reruns don't stack."""
    entities = sorted(set(source_counts) | set(indexed_counts))
    if not entities:
        spark.sql(
            f"""
            DELETE FROM {SUMMARY_TABLE}
            WHERE ns = '{ns}' AND run_date = DATE '{run_date}'
            """
        )
        log("summary_cleared", table=SUMMARY_TABLE, swap_completed=swap_completed)
        return
    values = ", ".join(
        "('{ns}', DATE '{run_date}', '{entity}', {source}, {indexed}, {match}, {swap})".format(
            ns=ns,
            run_date=run_date,
            entity=entity,
            source=source_counts.get(entity, 0),
            indexed=indexed_counts.get(entity, 0),
            match="true" if source_counts.get(entity, 0) == indexed_counts.get(entity, 0) else "false",
            swap="true" if swap_completed else "false",
        )
        for entity in entities
    )
    spark.sql(
        f"""
        INSERT INTO {SUMMARY_TABLE}
        REPLACE WHERE ns = '{ns}' AND run_date = DATE '{run_date}'
        VALUES {values}
        """
    )
    log("summary_written", table=SUMMARY_TABLE, source=source_counts, indexed=indexed_counts,
        swap_completed=swap_completed)


log("publish_started", indexed_at=indexed_at)

# COMMAND ----------

# Typed projection of the raw payloads, one row per entity id. The legacy script indexed
# whatever the API returned, so a duplicate page would double-index an entity; here the newest
# extract of each id wins.
spark.sql(
    f"""
    CREATE OR REPLACE TEMP VIEW search_reindex_projection AS
    SELECT ns, entity_type, entity_id, title, content, name, mime_type, folder_id,
           size_bytes, owner_id, tags, created_at, updated_at
    FROM (
      SELECT
        ns,
        entity_type,
        entity_id,
        get_json_object(payload, '$.title')                                        AS title,
        get_json_object(payload, '$.content')                                      AS content,
        coalesce(get_json_object(payload, '$.file_name'),
                 get_json_object(payload, '$.name'))                               AS name,
        get_json_object(payload, '$.mime_type')                                    AS mime_type,
        get_json_object(payload, '$.folder_id')                                    AS folder_id,
        try_cast(coalesce(get_json_object(payload, '$.size_bytes'),
                          get_json_object(payload, '$.size')) AS BIGINT)           AS size_bytes,
        get_json_object(payload, '$.owner_id')                                     AS owner_id,
        coalesce(from_json(get_json_object(payload, '$.tags'), 'array<string>'),
                 CAST(array() AS array<string>))                                  AS tags,
        try_cast(get_json_object(payload, '$.created_at') AS TIMESTAMP)            AS created_at,
        try_cast(get_json_object(payload, '$.updated_at') AS TIMESTAMP)            AS updated_at,
        row_number() OVER (
          PARTITION BY ns, entity_type, entity_id
          ORDER BY extracted_at DESC, get_json_object(payload, '$.updated_at') DESC
        ) AS rn
      FROM {BRONZE_TABLE}
      WHERE ns = '{ns}'
    )
    WHERE rn = 1
    """
)

spark.sql(
    f"""
    INSERT INTO {STAGING_TABLE}
    REPLACE WHERE ns = '{ns}'
    SELECT ns, entity_type, entity_id, title, content, name, mime_type, folder_id,
           size_bytes, owner_id, tags, created_at, updated_at,
           DATE '{run_date}' AS run_date,
           TIMESTAMP '{indexed_at}' AS indexed_at
    FROM search_reindex_projection
    """
)

source_counts = counts_by_entity(
    f"SELECT entity_type, COUNT(DISTINCT entity_id) AS n FROM {BRONZE_TABLE} WHERE ns = '{ns}' GROUP BY entity_type"
)
staged_counts = counts_by_entity(
    f"SELECT entity_type, COUNT(*) AS n FROM {STAGING_TABLE} WHERE ns = '{ns}' GROUP BY entity_type"
)
log("staging_built", table=STAGING_TABLE, source=source_counts, staged=staged_counts)

# COMMAND ----------

# Validate BEFORE touching the serving table. Either of these failing means the serving index
# keeps last week's rows -- stale beats empty, which is what the legacy cron delivered.
problems = []
if not staged_counts or sum(staged_counts.values()) == 0:
    problems.append("staging build is empty; refusing to replace a populated serving index")
divergent = {e: {"source": source_counts.get(e, 0), "staged": staged_counts.get(e, 0)}
             for e in set(source_counts) | set(staged_counts)
             if source_counts.get(e, 0) != staged_counts.get(e, 0)}
if divergent:
    problems.append(f"count divergence between bronze and staging: {divergent}")

if problems:
    write_summary(source_counts, staged_counts, swap_completed=False)
    log("publish_failed", problems=problems)
    raise RuntimeError("; ".join(problems))

# COMMAND ----------

column_list = ", ".join(INDEX_COLUMNS)
spark.sql(
    f"""
    INSERT INTO {SERVING_TABLE}
    REPLACE WHERE ns = '{ns}'
    SELECT {column_list} FROM {STAGING_TABLE} WHERE ns = '{ns}'
    """
)

serving_counts = counts_by_entity(
    f"SELECT entity_type, COUNT(*) AS n FROM {SERVING_TABLE} WHERE ns = '{ns}' GROUP BY entity_type"
)
duplicates = int(
    spark.sql(
        f"""
        SELECT COUNT(*) AS n FROM (
          SELECT entity_type, entity_id FROM {SERVING_TABLE} WHERE ns = '{ns}'
          GROUP BY entity_type, entity_id HAVING COUNT(*) > 1
        )
        """
    ).collect()[0]["n"]
)

post_swap_problems = []
if serving_counts != staged_counts:
    post_swap_problems.append(f"serving counts {serving_counts} do not match staged counts {staged_counts}")
if duplicates:
    post_swap_problems.append(f"{duplicates} duplicated entity ids in the serving index")

if post_swap_problems:
    write_summary(source_counts, serving_counts, swap_completed=True)
    log("swap_verification_failed", problems=post_swap_problems)
    raise RuntimeError("; ".join(post_swap_problems))

write_summary(source_counts, serving_counts, swap_completed=True)
log("swap_completed", table=SERVING_TABLE, counts=serving_counts, duplicate_entity_ids=duplicates)

dbutils.notebook.exit(json.dumps({
    "ns": ns,
    "run_date": run_date,
    "source_counts": source_counts,
    "indexed_counts": serving_counts,
    "counts_match": True,
    "swap_completed": True,
}))

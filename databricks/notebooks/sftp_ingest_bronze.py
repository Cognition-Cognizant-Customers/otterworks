# Databricks notebook source
# MAGIC %md
# MAGIC # `ow_tp_sftp_ingest` — bronze ingest of the mainframe CUSTBILL drops
# MAGIC
# MAGIC Converted from `etl/legacy-extra/jobs/sftp_ingest_poll.ksh` (ksh, 1998, ported 2014).
# MAGIC
# MAGIC The legacy job polled a drop directory three times, decided a transfer was
# MAGIC finished when `wc -c` returned the same number twice a second apart, copied the
# MAGIC file to `incoming/` and a timestamped `archive/`, suppressed every error with
# MAGIC `2>/dev/null || true`, and left a lock file behind on purpose.
# MAGIC
# MAGIC This notebook replaces all of that with:
# MAGIC
# MAGIC | Legacy | Here |
# MAGIC |---|---|
# MAGIC | hostname if-blocks choosing `/data/otterworks` vs `/data2/otterworks_uat` | `landing_root` / `catalog` / `ns` widgets, one code path |
# MAGIC | size-settle heuristic | whole-file read + SHA-256 manifest + HDR/TRL completeness gate |
# MAGIC | `.done` renames and a never-removed lock file | `MERGE` keyed on `(ns, file_name[, line_no])`, plus `max_concurrent_runs = 1` |
# MAGIC | `2>/dev/null \|\| true` | no suppression: an incomplete file raises and fails the run |
# MAGIC | trailer count logged, never reconciled | trailer count reconciled before anything is ingested |
# MAGIC
# MAGIC The statements themselves live in `scripts/tp_databricks/sftp_ingest_sql.py`,
# MAGIC deployed alongside this notebook as `/Shared/ow_tp/sftp_ingest_sql.py`, so the
# MAGIC job, the local driver, and the recon script all execute the same SQL.

# COMMAND ----------

import sys

WORKSPACE_DIR = "/Workspace/Shared/ow_tp"
if WORKSPACE_DIR not in sys.path:
    sys.path.append(WORKSPACE_DIR)

import sftp_ingest_sql  # noqa: E402 -- resolved from the deployed workspace directory

# COMMAND ----------

dbutils.widgets.text("ns", "demo", "Demo namespace")
dbutils.widgets.text("catalog", "ow_tp", "Unity Catalog catalog")
dbutils.widgets.text("landing_root", "", "Landing volume root (blank = the catalog's landing volume)")

# Job parameters end up inside SQL text, here and in the statement module, so they
# go through the same gate before anything is built from them: an `ns` carrying a
# quote must not be able to reach another namespace's objects on a shared workspace.
# The gate also rejects a landing_root outside the catalog's own volume, so this
# notebook cannot read one estate's drops into another estate's bronze.
_catalog = dbutils.widgets.get("catalog")
ns, catalog, landing_root = sftp_ingest_sql.validated(
    dbutils.widgets.get("ns"),
    _catalog,
    dbutils.widgets.get("landing_root") or sftp_ingest_sql.default_landing_root(_catalog),
)
print(f"ingesting ns={ns} catalog={catalog} landing_root={landing_root}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What landed
# MAGIC
# MAGIC Two conditions the legacy poller reported identically. An existing drop directory
# MAGIC with nothing new in it is a no-op success, as the legacy poll finding nothing was.
# MAGIC A drop directory that does not exist is a failure naming `ns` and the path it
# MAGIC expected: that namespace was never staged, or the transport writes somewhere else,
# MAGIC and reporting it as "no files today" is the habit this conversion retires.

# COMMAND ----------

try:
    landed = [r.file_name for r in spark.sql(sftp_ingest_sql.landed_files_query(ns, catalog, landing_root)).collect()]
except Exception as exc:
    missing = sftp_ingest_sql.as_missing_drop_path(exc, ns, landing_root)
    if missing is None:
        raise
    raise missing from exc

if not landed:
    print(f"no files under {sftp_ingest_sql.drop_path(ns, landing_root)}: nothing to ingest (no-op)")
    dbutils.notebook.exit(f"no-op: no files under {sftp_ingest_sql.drop_path(ns, landing_root)}")
print(f"landed files: {landed}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Completeness handshake
# MAGIC
# MAGIC A file is ingestable only when it carries exactly one HDR record, exactly one
# MAGIC TRL record, and a TRL-declared record count equal to the detail lines present.
# MAGIC A half-written transfer fails this gate, and the run fails with it — the legacy
# MAGIC job would have copied the partial file and moved on.

# COMMAND ----------

incomplete = spark.sql(sftp_ingest_sql.incomplete_files_query(ns, catalog, landing_root)).collect()
if incomplete:
    raise RuntimeError(
        "completeness handshake failed, refusing to ingest half-written files: "
        + "; ".join(
            f"{r.file_name} (hdr={r.header_lines}, trl={r.trailer_lines}, "
            f"detail={r.detail_lines}, declared={r.trailer_declared})"
            for r in incomplete
        )
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## Merge the raw lines and the manifest
# MAGIC
# MAGIC Idempotent: re-running for the same `ns` leaves both tables byte-identical,
# MAGIC including `ingested_at`, which only moves when a file's content changes.

# COMMAND ----------

for statement in sftp_ingest_sql.ingest_statements(ns, catalog, landing_root):
    spark.sql(statement)

# COMMAND ----------

summary = spark.sql(
    f"""
    SELECT f.file_name, f.size_bytes, f.sha256, f.record_count, count(l.line_no) AS lines_ingested
    FROM {catalog}.bronze.custbill_files f
    -- LEFT: a manifest row whose lines are all missing must show up as 0 and fail the
    -- reconciliation below, not disappear from it.
    LEFT JOIN {catalog}.bronze.custbill_lines l ON l.ns = f.ns AND l.file_name = f.file_name
    WHERE f.ns = '{ns}'
    GROUP BY f.file_name, f.size_bytes, f.sha256, f.record_count
    ORDER BY f.file_name
    """
)
display(summary)

# COMMAND ----------

# The run's own reconciliation: every manifest row must agree with the lines
# actually present. A mismatch here means the merge did not do what it claimed.
mismatched = [r for r in summary.collect() if r.record_count != r.lines_ingested]
if mismatched:
    raise RuntimeError(
        "manifest/lines mismatch after ingest: "
        + "; ".join(f"{r.file_name} manifest={r.record_count} lines={r.lines_ingested}" for r in mismatched)
    )
print(f"ingest complete for ns={ns}: {summary.count()} file(s)")

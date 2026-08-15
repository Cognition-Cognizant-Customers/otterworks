# Databricks notebook source
# MAGIC %md
# MAGIC # `ow_tp_parse_custbill` — CUSTBILL fixed-width parse (silver)
# MAGIC
# MAGIC Converted from `etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh` (bash +
# MAGIC `cut`/`sed`/`awk`, 2001, copybook CBCUST01). Run by the two serverless tasks of
# MAGIC the `ow_tp_parse_custbill` job:
# MAGIC
# MAGIC | task | `mode` | what it does |
# MAGIC |---|---|---|
# MAGIC | `wait_for_bronze_manifest` | `gate` | bronze manifest handshake; fails the run if the landing is incomplete |
# MAGIC | `parse` | `parse` | typed parse + quarantine + trailer reconciliation |
# MAGIC
# MAGIC What this retires, relative to the legacy script:
# MAGIC
# MAGIC - three passes of `sed`/`cut`/`awk` over `/tmp/cb_body.$$` → one pass over the
# MAGIC   bronze table, no temp files to orphan;
# MAGIC - no validation at all (bad records passed straight through into the finance
# MAGIC   report) → schema-validated typed columns, invalid records quarantined in
# MAGIC   `ow_tp.silver.custbill_rejects`;
# MAGIC - implied decimal and date by string surgery → `DECIMAL(18,2)` computed as
# MAGIC   `units / 100` and a real `DATE`, invalid dates rejected instead of reformatted;
# MAGIC - trailer count logged and ignored (ETL-0187, 2011) → reconciled in
# MAGIC   `ow_tp.silver.custbill_file_recon`, and a mismatch **fails the run**;
# MAGIC - `2>/dev/null || true` on every command, a lock file that was never removed, and
# MAGIC   hostname-selected `/data/otterworks` paths → errors raise, `max_concurrent_runs
# MAGIC   = 1` provides the mutual exclusion, and the namespace is a job parameter.

# COMMAND ----------

# MAGIC %md
# MAGIC The SQL lives in `databricks/notebooks/custbill_sql.py` and
# MAGIC `databricks/notebooks/custbill_parse_sql.py`, deployed alongside this notebook by
# MAGIC `make dbx-deploy-notebooks`. `%run` pulls in the same statement builders the
# MAGIC local driver and the recon script use, so the job and the reconciliation can
# MAGIC never execute different SQL.

# COMMAND ----------

# MAGIC %run ./custbill_sql

# COMMAND ----------

# MAGIC %run ./custbill_parse_sql

# COMMAND ----------

import re

dbutils.widgets.text("ns", "demo", "Demo namespace")
dbutils.widgets.dropdown("mode", "parse", ["gate", "parse"], "Task mode")

ns = dbutils.widgets.get("ns").strip()
mode = dbutils.widgets.get("mode").strip()

if re.fullmatch(r"[A-Za-z0-9_]+", ns) is None:
    raise ValueError(f"invalid namespace {ns!r}: expected [A-Za-z0-9_]+")

print(f"ns={ns} mode={mode}")

# COMMAND ----------


def run_gate(label, statements):
    """Fail the task if any assertion query returns rows.

    This is the behaviour the legacy chain never had: it logged the counts it
    could not reconcile and exited 0 regardless.
    """
    for name, statement in statements:
        offending = spark.sql(statement)
        count = offending.count()
        if count:
            offending.show(20, truncate=False)
            raise AssertionError(f"{label} gate failed: {name} ({count} offending rows)")
        print(f"ok: {name}")


# COMMAND ----------

if mode == "gate":
    # The manifest handshake replaces the legacy cron offset (:05 after a */15
    # ingest) and the "compare the file size twice, one second apart" settle
    # check: a half-written landing cannot satisfy it.
    run_gate("bronze manifest", gate_statements(ns))

elif mode == "parse":
    for statement in ddl_statements():
        spark.sql(statement)

    for name, statement in parse_statements(ns):
        spark.sql(statement)
        print(f"done: {name}")

    run_gate("trailer reconciliation", recon_gate_statements(ns))

else:
    raise ValueError(f"unknown mode {mode!r}: expected 'gate' or 'parse'")

# COMMAND ----------

if mode == "parse":
    display(spark.sql(f"SELECT * FROM {SILVER_FILE_RECON} WHERE ns = '{ns}' ORDER BY file_name"))
    display(
        spark.sql(
            f"""
            SELECT file_name, record_type, currency, count(*) AS records, sum(amount) AS amount
            FROM {SILVER_RECORDS}
            WHERE ns = '{ns}'
            GROUP BY file_name, record_type, currency
            ORDER BY file_name, record_type, currency
            """
        )
    )

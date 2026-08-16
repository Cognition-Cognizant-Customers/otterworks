# Converted `etl/legacy-extra/jobs/sftp_ingest_poll.ksh` (ksh, 1998 / ported 2014).
#
# Retires, in order, the deficiencies the addendum lists for this unit:
#   * hostname if-blocks picking /data/otterworks vs /data2/otterworks_uat  ->
#     `landing_root` is a job parameter, one code path for every environment;
#   * a lock file checked but never removed                                 ->
#     `max_concurrent_runs = 1` plus queueing: real mutual exclusion, no state
#     on disk that a crashed run can poison;
#   * "settling" a transfer by comparing `wc -c` twice, one second apart     ->
#     the ingest records a SHA-256 manifest row per file and merges on it, so a
#     half-written file is either absent or fully present, never half-ingested;
#   * `2>/dev/null || true` on every command                                 ->
#     nothing here suppresses errors; a failed task fails the run;
#   * no retention (archive/ grew forever, inputs renamed .done)             ->
#     the `retention` task below, driven by the `retention_days` parameter;
#   * credentials inline in the script                                      ->
#     the `ow_tp` secret scope from main.tf; this job needs none of its own.
#
# Zero-cost: the DDL and retention tasks run on the EXISTING serverless SQL
# warehouse (data source in main.tf), the ingest task on serverless job compute.
# Nothing here creates a cluster.

locals {
  sftp_ingest_job_name      = "${var.prefix}_sftp_ingest"
  sftp_ingest_notebook_path = "${databricks_directory.pipelines.path}/sftp_ingest_bronze"
  sftp_ingest_sql_dir       = "${databricks_directory.pipelines.path}/sql"
}

# The bronze DDL and the retention statements, as workspace files so the job
# tasks are plain SQL-file tasks with no compute of their own.
resource "databricks_workspace_file" "sftp_ingest_tables_sql" {
  source = "${path.module}/sql/sftp_ingest_bronze_tables.sql"
  path   = "${local.sftp_ingest_sql_dir}/sftp_ingest_bronze_tables.sql"

  depends_on = [databricks_directory.pipelines]
}

resource "databricks_workspace_file" "sftp_ingest_retention_sql" {
  source = "${path.module}/sql/sftp_ingest_retention.sql"
  path   = "${local.sftp_ingest_sql_dir}/sftp_ingest_retention.sql"

  depends_on = [databricks_directory.pipelines]
}

# The statement set the ingest runs, shared verbatim with the local driver and
# the recon script (scripts/tp_databricks/sftp_ingest_sql.py) so the job and the
# reconciliation can never drift apart. Added by the pipeline PR of this unit's
# stack; the notebook imports it from this workspace directory.
resource "databricks_workspace_file" "sftp_ingest_statements" {
  source = "${path.module}/../../scripts/tp_databricks/sftp_ingest_sql.py"
  path   = "${databricks_directory.pipelines.path}/sftp_ingest_sql.py"

  depends_on = [databricks_directory.pipelines]
}

resource "databricks_notebook" "sftp_ingest_bronze" {
  source   = "${path.module}/../../databricks/notebooks/sftp_ingest_bronze.py"
  path     = local.sftp_ingest_notebook_path
  language = "PYTHON"

  depends_on = [databricks_directory.pipelines]
}

resource "databricks_job" "sftp_ingest" {
  name        = local.sftp_ingest_job_name
  description = "Bronze ingest of the mainframe CUSTBILL drops: checksum manifest + raw record lines. Converted from etl/legacy-extra/jobs/sftp_ingest_poll.ksh."

  # The legacy lock file was checked, warned about, and never removed, so the
  # */15 cron happily overlapped itself (incident 2016-03-12). This is the
  # replacement, and it needs no cleanup after a crashed run.
  max_concurrent_runs = 1

  queue {
    enabled = true
  }

  parameter {
    name    = "ns"
    default = "demo"
  }

  parameter {
    name    = "landing_root"
    default = "/Volumes/${var.catalog_name}/bronze/landing"
  }

  parameter {
    name    = "catalog"
    default = var.catalog_name
  }

  # Retention for the bronze layer, replacing "archive/ grows forever".
  parameter {
    name    = "retention_days"
    default = "30"
  }

  task {
    task_key = "create_tables"

    # A failed task is a failed run: no `|| true` anywhere. Transient API or
    # compute failures get bounded retries, nothing is swallowed.
    max_retries               = 2
    min_retry_interval_millis = 60000

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.serverless.id

      file {
        path   = databricks_workspace_file.sftp_ingest_tables_sql.path
        source = "WORKSPACE"
      }

      parameters = {
        catalog = "{{job.parameters.catalog}}"
      }
    }
  }

  task {
    task_key = "ingest_bronze"

    depends_on {
      task_key = "create_tables"
    }

    max_retries               = 2
    min_retry_interval_millis = 60000

    # No compute block: serverless job compute. The notebook reads the landing
    # volume, hashes each file, and merges the manifest and the raw lines.
    notebook_task {
      notebook_path = databricks_notebook.sftp_ingest_bronze.path
      source        = "WORKSPACE"

      base_parameters = {
        ns           = "{{job.parameters.ns}}"
        catalog      = "{{job.parameters.catalog}}"
        landing_root = "{{job.parameters.landing_root}}"
      }
    }
  }

  task {
    task_key = "retention"

    depends_on {
      task_key = "create_tables"
    }

    depends_on {
      task_key = "ingest_bronze"
    }

    # Row retention must not hang off the ingest's result. An abandoned half-written
    # delivery fails `ingest_bronze` on every run (landing is the archive, so nothing
    # removes it), and on ALL_SUCCESS that failure would silently disable trimming for
    # the namespace forever — the unbounded-archive deficiency this conversion retires.
    # AT_LEAST_ONE_SUCCESS rather than ALL_DONE, because the tables have to exist: if
    # `create_tables` fails, `ingest_bronze` is skipped, no dependency succeeded, and
    # retention is skipped with it instead of deleting from a table that is not there.
    run_if = "AT_LEAST_ONE_SUCCESS"

    max_retries               = 2
    min_retry_interval_millis = 60000

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.serverless.id

      file {
        path   = databricks_workspace_file.sftp_ingest_retention_sql.path
        source = "WORKSPACE"
      }

      parameters = {
        catalog        = "{{job.parameters.catalog}}"
        ns             = "{{job.parameters.ns}}"
        retention_days = "{{job.parameters.retention_days}}"
      }
    }
  }

  # The legacy schedule was `*/15 * * * *` with overlapping runs. Kept as code
  # for fidelity but PAUSED: this is a shared demo workspace, and runs here are
  # triggered explicitly (`make dbx-run JOB=ow_tp_sftp_ingest NS=<ns>`).
  schedule {
    quartz_cron_expression = "0 0/15 * * * ?"
    timezone_id            = "UTC"
    pause_status           = "PAUSED"
  }

  # Matched to the shared driver's client-side wait deadline (dbx.run_job's
  # timeout_s = 1800): a server timeout longer than the client's turns a slow but
  # healthy run into a reported failure. A drop directory ingests in seconds, so
  # this is headroom either way.
  timeout_seconds = 1800
}

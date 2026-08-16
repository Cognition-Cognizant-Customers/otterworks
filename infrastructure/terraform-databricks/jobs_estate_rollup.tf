# Estate-level gold reconciliation and the dependency graph that replaces the
# legacy run_all.sh wrapper plus its clock-based crontab ordering.
#
# The rollup writes one reconciled slice for every converted unit and records
# planted seed-manifest anomalies. The orchestrator then makes each unit's
# completion a real graph edge, so a failed child cannot leave the estate green.

variable "estate_rollup_alert_emails" {
  description = "Addresses notified when the estate rollup or its reconciliation fails. Empty by default because the shared demo workspace has no committed recipient."
  type        = list(string)
  default     = []
}

locals {
  estate_rollup_notebook_source = "${path.module}/../../databricks/notebooks/estate_rollup.py"
  estate_rollup_enabled         = fileexists(local.estate_rollup_notebook_source)

  estate_rollup_leaf_tasks = concat(
    local.finance_report_enabled ? ["finance_report"] : ["parse_custbill"],
    ["user_activity", "audit_archive", "search_reindex", "storage_cleanup"],
  )
}

# The DDL is catalog-relative, so the SQL task's catalog parameter keeps the
# warehouse context aligned with the catalog used by the notebook and avoids
# the hardcoded-catalog precondition needed by older unit DDL files.
resource "databricks_workspace_file" "estate_rollup_tables" {
  source = "${path.module}/../../databricks/sql/estate_rollup_tables.sql"
  path   = "${databricks_directory.pipelines.path}/sql/estate_rollup_tables.sql"

  depends_on = [databricks_directory.pipelines]
}

# PR2 owns the notebook. Gating both resources on the source keeps this shared
# configuration plan-safe now and makes the job appear automatically when the
# reviewed pipeline file lands, instead of requiring an operator flag.
resource "databricks_notebook" "estate_rollup" {
  count = local.estate_rollup_enabled ? 1 : 0

  source   = local.estate_rollup_notebook_source
  path     = "${databricks_directory.pipelines.path}/estate_rollup"
  format   = "SOURCE"
  language = "PYTHON"
}

resource "databricks_job" "estate_rollup" {
  count = local.estate_rollup_enabled ? 1 : 0

  name        = "${var.prefix}_estate_rollup"
  description = "Estate-level gold reconciliation and seed-manifest anomaly evidence for ${var.catalog_name}."

  depends_on = [
    databricks_directory.pipelines,
    databricks_workspace_file.estate_rollup_tables,
  ]

  # A rerun replaces only its (ns, run_date) slice, so serialization retires
  # the legacy overlapping-run risk without making reconciliation non-idempotent.
  max_concurrent_runs = 1
  timeout_seconds     = 3600

  queue {
    enabled = true
  }

  parameter {
    name    = "ns"
    default = "demo"
  }

  parameter {
    name    = "catalog"
    default = var.catalog_name
  }

  # The start date is resolved once at the job boundary, so retries and both
  # tasks in one run cannot cross midnight and reconcile different slices.
  parameter {
    name    = "run_date"
    default = "{{job.start_time.iso_date}}"
  }

  parameter {
    name    = "job_run_id"
    default = "{{job.run_id}}"
  }

  task {
    task_key                  = "ddl"
    max_retries               = 2
    min_retry_interval_millis = 60000
    retry_on_timeout          = false

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.serverless.id

      file {
        path   = databricks_workspace_file.estate_rollup_tables.path
        source = "WORKSPACE"
      }

      # The provider exposes SQL-task execution context through its parameters
      # map; pass the catalog explicitly so the catalog-relative DDL runs in the
      # same catalog as the rollup notebook.
      parameters = {
        catalog = var.catalog_name
      }
    }
  }

  task {
    task_key = "estate_rollup"

    depends_on {
      task_key = "ddl"
    }

    notebook_task {
      notebook_path = databricks_notebook.estate_rollup[0].path
      source        = "WORKSPACE"

      base_parameters = {
        ns         = "{{job.parameters.ns}}"
        catalog    = "{{job.parameters.catalog}}"
        run_date   = "{{job.parameters.run_date}}"
        job_run_id = "{{job.parameters.job_run_id}}"
      }
    }

    # The notebook raises after recording non-green units; bounded retries are
    # safe because its INSERT ... REPLACE WHERE write is idempotent.
    max_retries               = 2
    min_retry_interval_millis = 60000
    retry_on_timeout          = true
    timeout_seconds           = 3600
  }

  dynamic "email_notifications" {
    for_each = length(var.estate_rollup_alert_emails) == 0 ? [] : [1]

    content {
      on_failure = var.estate_rollup_alert_emails
    }
  }

  tags = {
    project = "otterworks-tp"
    unit    = "estate_rollup"
    layer   = "gold"
  }
}

resource "databricks_job" "estate_orchestrator" {
  name        = "${var.prefix}_estate_orchestrator"
  description = "Dependency-driven estate orchestration replacing etl/legacy-extra/run_all.sh and its legacy crontab."

  # One estate run at a time replaces stale lock files and overlapping cron
  # invocations; queued runs remain visible instead of being silently dropped.
  max_concurrent_runs = 1

  queue {
    enabled = true
  }

  parameter {
    name    = "ns"
    default = "demo"
  }

  parameter {
    name    = "catalog"
    default = var.catalog_name
  }

  parameter {
    name    = "run_date"
    default = "{{job.start_time.iso_date}}"
  }

  schedule {
    # Replaces the legacy nightly 02:00-05:00 crontab window for analytics,
    # audit, search, cleanup, activity, ingest, parse, and finance. The estate
    # schedule is paused until an operator explicitly rehearses the migration.
    quartz_cron_expression = "0 0 2 * * ?"
    timezone_id            = "UTC"
    pause_status           = "PAUSED"
  }

  task {
    task_key = "sftp_ingest"

    run_job_task {
      job_id = databricks_job.sftp_ingest.id

      job_parameters = {
        ns      = "{{job.parameters.ns}}"
        catalog = "{{job.parameters.catalog}}"
      }
    }
  }

  task {
    task_key = "parse_custbill"

    depends_on {
      task_key = "sftp_ingest"
    }

    run_job_task {
      job_id = databricks_job.parse_custbill.id

      job_parameters = {
        ns      = "{{job.parameters.ns}}"
        catalog = "{{job.parameters.catalog}}"
      }
    }
  }

  dynamic "task" {
    for_each = local.finance_report_enabled ? [1] : []

    content {
      task_key = "finance_report"

      depends_on {
        task_key = "parse_custbill"
      }

      run_job_task {
        job_id = databricks_job.finance_report[0].id

        job_parameters = {
          ns          = "{{job.parameters.ns}}"
          report_date = "{{job.parameters.run_date}}"
          catalog     = "{{job.parameters.catalog}}"
        }
      }
    }
  }

  task {
    task_key = "analytics_daily"

    run_job_task {
      job_id = databricks_job.analytics_daily.id

      job_parameters = {
        ns      = "{{job.parameters.ns}}"
        catalog = "{{job.parameters.catalog}}"
      }
    }
  }

  task {
    task_key = "user_activity"

    depends_on {
      task_key = "analytics_daily"
    }

    run_job_task {
      job_id = databricks_job.user_activity.id

      job_parameters = {
        ns          = "{{job.parameters.ns}}"
        report_date = "{{job.parameters.run_date}}"
      }
    }
  }

  task {
    task_key = "audit_archive"

    run_job_task {
      job_id = databricks_job.audit_archive.id

      job_parameters = {
        ns       = "{{job.parameters.ns}}"
        run_date = "{{job.parameters.run_date}}"
        catalog  = "{{job.parameters.catalog}}"
      }
    }
  }

  task {
    task_key = "search_reindex"

    run_job_task {
      job_id = databricks_job.search_reindex.id

      job_parameters = {
        ns       = "{{job.parameters.ns}}"
        run_date = "{{job.parameters.run_date}}"
        catalog  = "{{job.parameters.catalog}}"
      }
    }
  }

  task {
    task_key = "storage_cleanup"

    run_job_task {
      job_id = databricks_job.storage_cleanup.id

      job_parameters = {
        ns      = "{{job.parameters.ns}}"
        catalog = "{{job.parameters.catalog}}"
      }
    }
  }

  dynamic "task" {
    for_each = local.estate_rollup_enabled ? [1] : []

    content {
      task_key = "estate_rollup"

      dynamic "depends_on" {
        for_each = local.estate_rollup_leaf_tasks

        content {
          task_key = depends_on.value
        }
      }

      run_job_task {
        job_id = databricks_job.estate_rollup[0].id

        job_parameters = {
          ns         = "{{job.parameters.ns}}"
          catalog    = "{{job.parameters.catalog}}"
          run_date   = "{{job.parameters.run_date}}"
          job_run_id = "{{job.run_id}}"
        }
      }
    }
  }

  tags = {
    project = "otterworks-tp"
    unit    = "estate_orchestrator"
  }
}

# Converted `etl/scripts/audit_archive_weekly.py` (Sunday 03:00 UTC cron) as a
# Unity Catalog job. The legacy script scanned the whole DynamoDB audit table
# for events past a 90-day horizon, uploaded a JSONL.gz to Glacier and then
# batch-deleted the source rows with the deletes wrapped in `except: pass` --
# so a failed upload, or a failed delete, was indistinguishable from success.
#
# What this definition changes: retention is a job parameter instead of a
# constant in the source, the run is serverless with retries and failure
# notifications instead of an unmonitored cron, and the archive/verify/delete
# ordering is enforced by the pipeline and by a CHECK constraint on the gold
# manifest (see databricks/sql/audit_archive_ddl.sql).
#
# No compute is created here: the DDL task runs on the existing serverless SQL
# warehouse and the pipeline task on serverless job compute.

variable "audit_archive_alert_emails" {
  description = "Addresses alerted when the audit-archive job fails or overruns. Empty by default so the demo estate sends no mail unless asked; the legacy cron alerted no one at all."
  type        = list(string)
  default     = []
}

variable "audit_archive_retention_days" {
  description = "Retention horizon for audit events, in days. The legacy job hardcoded 90 in the script body; here it is the job's parameter and is recorded on every archived row."
  type        = number
  default     = 90
}

# Table DDL as code, deployed into the demo's workspace directory so the job's
# first task can apply it. Idempotent, so every run reconciles the schema.
#
# A SQL file task cannot substitute identifiers, so the DDL spells the catalog
# out. That makes `var.catalog_name` and this file two sources of truth for the
# same name: with a renamed catalog the DDL would build the tables somewhere the
# rest of the job never reads. Fail the plan instead of discovering it at
# runtime -- renaming the catalog means templating the DDL too.
resource "databricks_workspace_file" "audit_archive_ddl" {
  source = "${path.module}/../../databricks/sql/audit_archive_ddl.sql"
  path   = "${databricks_directory.pipelines.path}/sql/audit_archive_ddl.sql"

  lifecycle {
    precondition {
      condition     = var.catalog_name == "ow_tp"
      error_message = "databricks/sql/audit_archive_ddl.sql names ow_tp.{bronze,silver,gold} literally, so catalog_name=${var.catalog_name} would create the audit-archive tables in a catalog the job does not read. Template the DDL before renaming the catalog."
    }
  }
}

resource "databricks_job" "audit_archive" {
  name        = "${var.prefix}_audit_archive"
  description = "Weekly audit-event retention: archive events past the horizon, verify the archive, then purge the source."

  # The legacy cron could overlap itself; a second concurrent run here would
  # race the verify/delete step.
  max_concurrent_runs = 1
  timeout_seconds     = 3600

  parameter {
    name    = "ns"
    default = "demo"
  }

  # Empty means "today, UTC" (the legacy job's implicit behaviour); setting it
  # makes a run reproducible and backfillable, which cron never was.
  parameter {
    name    = "run_date"
    default = ""
  }

  parameter {
    name    = "retention_days"
    default = tostring(var.audit_archive_retention_days)
  }

  parameter {
    name    = "catalog"
    default = var.catalog_name
  }

  parameter {
    name    = "source_path"
    default = "/Volumes/${var.catalog_name}/bronze/landing"
  }

  task {
    task_key = "create_tables"

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.serverless.id

      file {
        path   = databricks_workspace_file.audit_archive_ddl.path
        source = "WORKSPACE"
      }
    }
  }

  schedule {
    quartz_cron_expression = "0 0 3 ? * SUN *" # Sunday 03:00 UTC, as the legacy crontab
    timezone_id            = "UTC"
    # Demo estate: the schedule is declared so the migration is complete, but
    # left paused so nothing runs unattended between rehearsals.
    pause_status = "PAUSED"
  }

  # Failures were previously discovered by reading /var/log/etl by hand.
  # A health rule only marks the run as having breached the threshold; the mail
  # goes out through the matching notification field, so both are wired.
  email_notifications {
    on_failure                             = var.audit_archive_alert_emails
    on_duration_warning_threshold_exceeded = var.audit_archive_alert_emails
  }

  health {
    rules {
      metric = "RUN_DURATION_SECONDS"
      op     = "GREATER_THAN"
      value  = 1800
    }
  }
}

output "audit_archive_job_url" {
  description = "Converted audit-archive job in the workspace."
  value       = databricks_job.audit_archive.url
}

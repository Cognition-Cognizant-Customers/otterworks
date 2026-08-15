# Converted `etl/scripts/analytics_daily.py` (daily 02:00 UTC cron) as a job definition.
#
# The legacy cron was scheduled by crontab with no dependency management, overlapped the
# 02:10 finance report, retried nothing, alerted nobody, and -- its headline defect --
# treated three consecutive SQS failures as "give up, continue with zero events, report
# success". The job below retires that operationally: bounded retries with a backoff on
# transient failures, one run at a time, and failure notifications instead of a green exit
# code on an empty extract (the pipeline itself fails a zero-event load; see
# databricks/notebooks/analytics_daily.py).
#
# No compute is created: the notebook task carries no cluster spec, so it runs on
# serverless job compute, and all SQL runs on the pre-existing serverless warehouse.

variable "analytics_daily_alert_emails" {
  description = "Addresses notified when ow_tp_analytics_daily fails or its extract is empty. Empty by default: the demo workspace is shared, so no address is committed."
  type        = list(string)
  default     = []
}

resource "databricks_job" "analytics_daily" {
  name        = "${var.prefix}_analytics_daily"
  description = "Daily analytics aggregation: SQS/DynamoDB/S3 event extracts -> bronze/silver/gold in ${var.catalog_name}. Converted from etl/scripts/analytics_daily.py."

  depends_on = [
    databricks_schema.layer,
    databricks_volume.landing,
    databricks_directory.pipelines,
  ]

  # The legacy cron could overlap itself and the 02:10 finance report; queued serial runs
  # replace that with an explicit single-writer guarantee (the loads are ns-scoped
  # replacements, so a concurrent second run would fight over the same slice).
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

  # Landing area for the event extracts, replacing the hardcoded production SQS queue URL
  # and DynamoDB table name in the legacy source.
  parameter {
    name    = "source_glob"
    default = "/Volumes/${var.catalog_name}/bronze/landing/{ns}/analytics_daily/events/"
  }

  parameter {
    name    = "source_kind"
    default = "s3"
  }

  parameter {
    name    = "ddl_path"
    default = "/Volumes/${var.catalog_name}/bronze/landing/{ns}/analytics_daily/ddl/analytics_daily.sql"
  }

  task {
    task_key = "analytics_daily"

    notebook_task {
      notebook_path = "${databricks_directory.pipelines.path}/analytics_daily"
      source        = "WORKSPACE"
    }

    # Transient source failures are retried here and inside the notebook; what is never
    # retried into silence is an empty extract -- that fails the run.
    max_retries              = 2
    min_retry_interval_millis = 60000
    retry_on_timeout          = true
  }

  # Replaces `0 2 * * *` in the legacy crontab. Paused in code: the workspace is shared
  # with other demos, so runs are triggered explicitly per rehearsal.
  schedule {
    quartz_cron_expression = "0 0 2 * * ?"
    timezone_id            = "UTC"
    pause_status           = "PAUSED"
  }

  dynamic "email_notifications" {
    for_each = length(var.analytics_daily_alert_emails) > 0 ? [1] : []

    content {
      on_failure = var.analytics_daily_alert_emails
      # A run that ends with no events is a failure, not a success, so on_failure covers
      # the legacy silent-data-loss case as well.
      no_alert_for_skipped_runs = true
    }
  }

  tags = {
    project = "otterworks-tp"
    unit    = "analytics_daily"
    legacy  = "etl/scripts/analytics_daily.py"
  }
}

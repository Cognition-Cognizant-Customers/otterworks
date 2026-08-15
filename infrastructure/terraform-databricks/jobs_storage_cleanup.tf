# Converted `etl/scripts/storage_cleanup_daily.py` (daily 02:30 UTC cron on a
# single EC2 box) as a Databricks job. Serverless notebook tasks only: no
# cluster, no hourly floor. The notebook lives in PR 2 of this series and is
# deployed with `make dbx-deploy-notebooks`.
#
# The legacy script had no dry-run and no guard: a DynamoDB read failure looked
# exactly like "these objects are orphans", so a transient metadata problem
# quarantined live customer files. The conversion makes both explicit —
# `dry_run` is a job parameter that defaults to true, and the pipeline records a
# metadata_read_ok verdict that gates quarantining entirely.

# Declared here rather than in the shared variables.tf: this unit contributes
# exactly one file to the stack.
variable "alert_emails" {
  description = "Addresses notified when a converted job run fails. Empty by default so the shared demo workspace mails nobody unless the operator opts in."
  type        = list(string)
  default     = []
}

locals {
  storage_cleanup_job_name = "${var.prefix}_storage_cleanup"
  storage_cleanup_notebook = "${databricks_directory.pipelines.path}/storage_cleanup_daily"
}

resource "databricks_job" "storage_cleanup" {
  name        = local.storage_cleanup_job_name
  description = "Orphaned-object detection and quarantine decision for the demo namespace, converted from the 2014 storage_cleanup_daily.py cron."

  # The legacy cron could overlap itself on a slow S3 listing; two concurrent
  # quarantine passes over the same bucket is exactly the failure mode the
  # conversion must not inherit.
  max_concurrent_runs = 1

  parameter {
    name    = "ns"
    default = "demo"
  }

  # Safe by default: a run only quarantines when it is asked to AND the metadata
  # read was complete (enforced in the pipeline, not by convention).
  parameter {
    name    = "dry_run"
    default = "true"
  }

  # Which extract under <ns>/storage_cleanup/ in the landing volume to load.
  # The safety-guard demonstration points this at a deliberately truncated
  # metadata extract.
  parameter {
    name    = "input_dir"
    default = "storage_cleanup"
  }

  parameter {
    name    = "scenario"
    default = "nominal"
  }

  task {
    task_key = "create_tables"

    notebook_task {
      notebook_path = local.storage_cleanup_notebook
      base_parameters = {
        stage = "ddl"
      }
    }

    # Transient metadata/warehouse errors retried instead of swallowed by
    # `except: pass`.
    max_retries               = 2
    min_retry_interval_millis = 60000
  }

  task {
    task_key = "cleanup_pipeline"

    depends_on {
      task_key = "create_tables"
    }

    notebook_task {
      notebook_path = local.storage_cleanup_notebook
      base_parameters = {
        stage = "pipeline"
      }
    }

    max_retries               = 2
    min_retry_interval_millis = 60000
  }

  schedule {
    # Legacy: `30 2 * * *` in /etc/crontab on the ETL box.
    quartz_cron_expression = "0 30 2 * * ?"
    timezone_id            = "UTC"
    # The parent session owns when this estate is live; a scheduled demo job
    # firing unattended in a shared workspace is not free of consequences.
    pause_status = "PAUSED"
  }

  # Replaces "failures are discovered when someone greps /var/log/etl".
  email_notifications {
    on_failure = var.alert_emails
  }

  health {
    rules {
      metric = "RUN_DURATION_SECONDS"
      op     = "GREATER_THAN"
      value  = 3600
    }
  }
}

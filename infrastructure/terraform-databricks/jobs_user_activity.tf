# Converted job for etl/scripts/user_activity_daily.py (legacy daily 05:00 UTC cron).
#
# The legacy script consumed analytics_daily.py's output — the PostgreSQL table
# analytics_daily_summary and the per-user analytics/daily/.../top_users.jsonl.gz
# files — with no check that the upstream job had run: cron ordered the two by
# clock alone (02:00, then 05:00), so a late or failed analytics run silently
# produced a report over stale or partial data.
#
# The dependency is expressed here instead of assumed:
#   * assert_upstream_fresh runs first and fails the run when the upstream
#     summary is missing, empty for the namespace, or does not cover the newest
#     landed event date;
#   * build_report depends on it, so no report is produced over stale data;
#   * when the analytics unit's job exists, set analytics_job_id and the run
#     triggers it first, making the ordering a real graph edge rather than a
#     clock coincidence.
#
# Zero-cost: notebook tasks carry no cluster spec, so they run on serverless job
# compute; the schedule mirrors the legacy 05:00 UTC cron but ships PAUSED.

variable "user_activity_ns" {
  description = "Demo namespace the converted user-activity job defaults to. Table rows and volume paths are namespaced by it."
  type        = string
  default     = "demo"
}

variable "user_activity_lookback_days" {
  description = "Report lookback window, matching the legacy script's hardcoded 30 days."
  type        = number
  default     = 30
}

variable "user_activity_upstream_table" {
  description = "Upstream analytics aggregate the report reads, as catalog-relative schema.table: the analytics unit's gold table, replacing the legacy analytics_daily_summary PostgreSQL table. Qualified with var.catalog_name so a non-default catalog stays consistent."
  type        = string
  default     = "gold.analytics_daily_summary"
}

variable "user_activity_max_upstream_lag_days" {
  description = "Freshness tolerance: how far behind report_date the upstream aggregate's latest report_date may be before the run is refused. Both jobs are daily, so anything above 1 would re-admit the stale-report failure this conversion exists to remove; it is a variable only so a backfill can widen it deliberately."
  type        = number
  default     = 1
}

variable "analytics_job_id" {
  description = "Job id of the converted analytics_daily job. When set (non-zero), this job triggers it first so the upstream dependency is a graph edge, not a cron assumption. Left unset until that unit lands."
  type        = number
  default     = 0
}

locals {
  user_activity_notebook = "${databricks_directory.pipelines.path}/user_activity_daily"

  user_activity_base_parameters = {
    # Both taken from the job parameters, not the Terraform defaults: a run started for
    # another namespace must be gated and published under that namespace, not silently
    # overwrite the default one's partitions.
    ns = "{{job.parameters.ns}}"
    # The run's UTC start date, resolved once for the whole run by the job parameter's
    # default: both tasks (and any retry of either) then gate and publish the same date.
    # Letting each task resolve "today" itself would re-admit a clock race across midnight.
    report_date            = "{{job.parameters.report_date}}"
    lookback_days          = tostring(var.user_activity_lookback_days)
    upstream_summary_table = "${var.catalog_name}.${var.user_activity_upstream_table}"
    max_upstream_lag_days  = tostring(var.user_activity_max_upstream_lag_days)
    catalog                = var.catalog_name
  }

  user_activity_upstream_task = var.analytics_job_id != 0 ? ["run_analytics_upstream"] : []
}

resource "databricks_job" "user_activity" {
  name                = "${var.prefix}_user_activity"
  description         = "Daily per-user activity report: converted from etl/scripts/user_activity_daily.py, with the upstream-freshness gate the legacy cron could not express."
  max_concurrent_runs = 1

  # Idempotent by (ns, report_date): a re-run overwrites its own partition, so a
  # retry can never double-count. The legacy script had no such guarantee.
  queue {
    enabled = true
  }

  schedule {
    quartz_cron_expression = "0 0 5 * * ?" # legacy cron: daily 05:00 UTC
    timezone_id            = "UTC"
    pause_status           = "PAUSED"
  }

  parameter {
    name    = "ns"
    default = var.user_activity_ns
  }

  parameter {
    name    = "report_date"
    default = "{{job.start_time.iso_date}}"
  }

  # Optional first hop: trigger the upstream analytics job so ordering is a real
  # dependency. Only created once that unit's job id is known.
  dynamic "task" {
    for_each = local.user_activity_upstream_task

    content {
      task_key = task.value

      run_job_task {
        job_id = var.analytics_job_id
        # The run's own ns and date are forwarded: the gate below reads the upstream
        # table filtered by them, so an upstream refreshed under its own defaults
        # would leave a non-default namespace refused as if it had never run.
        job_parameters = {
          ns          = "{{job.parameters.ns}}"
          report_date = "{{job.parameters.report_date}}"
        }
      }
    }
  }

  task {
    task_key = "assert_upstream_fresh"

    dynamic "depends_on" {
      for_each = local.user_activity_upstream_task

      content {
        task_key = depends_on.value
      }
    }

    notebook_task {
      notebook_path = local.user_activity_notebook
      base_parameters = merge(local.user_activity_base_parameters, {
        stage = "freshness_gate"
      })
    }

    # Retries retire the legacy script's "no retry, no alert" behaviour without
    # risking duplicate output: every stage is idempotent.
    max_retries               = 2
    min_retry_interval_millis = 60000
    timeout_seconds           = 1800
  }

  task {
    task_key = "build_report"

    depends_on {
      task_key = "assert_upstream_fresh"
    }

    notebook_task {
      notebook_path = local.user_activity_notebook
      base_parameters = merge(local.user_activity_base_parameters, {
        stage = "pipeline"
      })
    }

    max_retries               = 2
    min_retry_interval_millis = 60000
    timeout_seconds           = 3600
  }

  health {
    rules {
      metric = "RUN_DURATION_SECONDS"
      op     = "GREATER_THAN"
      value  = 3600
    }
  }
}

output "user_activity_job_url" {
  description = "Converted user-activity job in the shared workspace."
  value       = databricks_job.user_activity.url
}

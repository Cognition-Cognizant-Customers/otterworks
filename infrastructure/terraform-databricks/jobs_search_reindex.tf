# Converted unit: etl/scripts/search_reindex_weekly.py -> ow_tp_search_reindex
#
# The legacy cron deleted the MeiliSearch `documents` and `files` indices as its
# first act, then paginated document-service and file-service to refill them. A
# single failed extract page therefore left search empty in production. The
# converted job never destroys the serving copy: `ingest_bronze` lands the raw
# extracts, `publish_index` builds the projection into a staging table,
# validates counts against the landed source, and only then swaps it into
# ow_tp.silver.search_index_documents in one atomic partition replace. A count
# mismatch fails the run instead of logging a line, and a failed extract leaves
# the previously published index serving.
#
# Tables are defined in databricks/sql/search_reindex_tables.sql (idempotent);
# apply them with scripts/tp_databricks/apply_sql.py before the first run.

variable "search_reindex_alert_emails" {
  description = "Addresses alerted when ow_tp_search_reindex fails or its counts diverge. The legacy cron had no alerting at all: failures surfaced only when someone noticed search was empty."
  type        = list(string)
  default     = []
}

resource "databricks_job" "search_reindex" {
  name        = "${var.prefix}_search_reindex"
  description = "Weekly rebuild of the search index projection (converted from etl/scripts/search_reindex_weekly.py). Build-then-swap: the serving table is replaced only after counts reconcile."

  # The legacy schedule (Sunday 04:00 UTC), paused because the demo drives runs
  # explicitly per namespace.
  schedule {
    quartz_cron_expression = "0 0 4 ? * SUN *"
    timezone_id            = "UTC"
    pause_status           = "PAUSED"
  }

  # Overlapping reindexes cannot interleave a swap with a build.
  max_concurrent_runs = 1

  queue {
    enabled = true
  }

  parameter {
    name    = "ns"
    default = "demo"
  }

  # Empty means "the run's own date"; set explicitly to re-publish a past run.
  parameter {
    name    = "run_date"
    default = ""
  }

  # Landing prefix holding the extracts for this namespace, written by
  # scripts/tp_databricks/extract_search_sources.py.
  parameter {
    name    = "landing_prefix"
    default = "search_reindex"
  }

  # Forces the source read to fail, to demonstrate that a broken source leaves
  # the published index intact (contract acceptance check 3).
  parameter {
    name    = "simulate_source_failure"
    default = "false"
  }

  parameter {
    name    = "catalog"
    default = var.catalog_name
  }

  task {
    task_key = "ingest_bronze"

    notebook_task {
      notebook_path = "${databricks_directory.pipelines.path}/search_reindex_ingest"
      source        = "WORKSPACE"
    }

    # Serverless job compute: no cluster block, nothing with an hourly floor.
    timeout_seconds           = 1800
    max_retries               = 2
    min_retry_interval_millis = 60000
    retry_on_timeout          = true
  }

  task {
    task_key = "publish_index"

    depends_on {
      task_key = "ingest_bronze"
    }

    notebook_task {
      notebook_path = "${databricks_directory.pipelines.path}/search_reindex_publish"
      source        = "WORKSPACE"
    }

    # Retryable because build-validate-swap is idempotent per (ns, run_date).
    timeout_seconds           = 1800
    max_retries               = 1
    min_retry_interval_millis = 60000
    retry_on_timeout          = true
  }

  email_notifications {
    on_failure = var.search_reindex_alert_emails
  }

  notification_settings {
    no_alert_for_skipped_runs = true
  }

  tags = {
    project = "otterworks-tp"
    unit    = "search_reindex_weekly"
  }
}

output "search_reindex_job" {
  description = "Converted search reindex job: name and workspace URL."
  value = {
    name = databricks_job.search_reindex.name
    url  = databricks_job.search_reindex.url
  }
}

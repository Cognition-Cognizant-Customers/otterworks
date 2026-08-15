# Converted replacement for etl/legacy-extra/jobs/finance_excel_report.pl (Perl, 2004):
# the gold finance billing summary plus the delivery audit the legacy sendmail no-op
# never produced. Contract: docs/tech-partnerships/contracts/finance_excel_report.md.
#
# Compute is serverless job compute (no cluster block anywhere in this file) and the
# notebook's SQL runs against Unity Catalog, so nothing here has an hourly floor.

variable "finance_report_recipients" {
  description = "Managed finance distribution list, stored in the ow_tp secret scope instead of hardcoded in the report script (the legacy list still pointed at an address that left in 2020)."
  type        = string
  default     = "finance-reports@otterworks.dev"
}

variable "finance_report_alert_emails" {
  description = "Addresses notified when the report run fails. The legacy job suppressed every error, so nobody was told."
  type        = list(string)
  default     = []
}

variable "finance_report_parse_job_id" {
  description = "Job id of ow_tp_parse_custbill. When set, the report waits on a fresh parse run instead of the legacy 02:10-after-a-sleep-600 guess. Null until that unit's job is applied."
  type        = string
  default     = null
}

variable "finance_report_schedule_status" {
  description = "PAUSED or UNPAUSED. Paused by default: this is a shared demo workspace and runs are driven on demand."
  type        = string
  default     = "PAUSED"
}

# Recipients are configuration, not code. The report job resolves them at run time from
# this scope, so changing the distribution list never means editing a script again.
resource "databricks_secret" "finance_report_recipients" {
  scope        = databricks_secret_scope.estate.name
  key          = "finance_report_recipients"
  string_value = var.finance_report_recipients
}

locals {
  finance_report_notebook_source = "${path.module}/../../databricks/notebooks/tp_finance_report.py"

  # Terraform plans this whole directory as one configuration, so a notebook source that
  # is not on disk yet would abort the apply for the entire shared estate. The job is
  # declared conditionally on its own source instead, and starts existing the moment the
  # pipeline code lands -- no operator flag to remember to flip.
  finance_report_enabled = fileexists(local.finance_report_notebook_source)
}

resource "databricks_notebook" "finance_report" {
  count = local.finance_report_enabled ? 1 : 0

  source = local.finance_report_notebook_source
  path   = "${databricks_directory.pipelines.path}/tp_finance_report"
  format = "SOURCE"
}

resource "databricks_job" "finance_report" {
  count = local.finance_report_enabled ? 1 : 0

  name        = "${var.prefix}_finance_report"
  description = "Gold finance billing summary by currency and record type, aggregated from ow_tp.silver.custbill_records, plus an explicit delivery audit row."

  # The legacy estate ran this at 02:10 daily, overlapping analytics_daily on the same
  # box, with a lock file that was checked and never removed. One run at a time, and
  # finance's 2018 request for 06:00 (ticket lost) is honoured.
  max_concurrent_runs = 1

  schedule {
    quartz_cron_expression = "0 0 6 * * ?"
    timezone_id            = "UTC"
    pause_status           = var.finance_report_schedule_status
  }

  queue {
    enabled = true
  }

  parameter {
    name    = "ns"
    default = "demo"
  }

  parameter {
    name    = "report_date"
    default = "" # empty -> the run date, as the legacy job's localtime stamp did
  }

  parameter {
    name    = "catalog"
    default = var.catalog_name
  }

  parameter {
    name    = "secret_scope"
    default = var.prefix
  }

  parameter {
    name    = "recipients_secret_key"
    default = databricks_secret.finance_report_recipients.key
  }

  parameter {
    name    = "smtp_secret_key"
    default = "finance_report_smtp_host"
  }

  # Explicit upstream dependency, replacing run_all.sh's `sleep 600`: when the parse
  # unit's job exists, the report runs only after a successful parse run.
  dynamic "task" {
    for_each = var.finance_report_parse_job_id == null ? [] : [var.finance_report_parse_job_id]

    content {
      task_key = "parse_custbill"

      run_job_task {
        job_id = task.value
      }
    }
  }

  task {
    task_key            = "finance_summary"
    depends_on          = var.finance_report_parse_job_id == null ? [] : [{ task_key = "parse_custbill" }]
    max_retries         = 2
    min_retry_interval_millis = 60000
    timeout_seconds     = 1800

    notebook_task {
      notebook_path = databricks_notebook.finance_report[0].path
      source        = "WORKSPACE"
    }
  }

  # Failures surface instead of being swallowed by `2>/dev/null` and $SIG{PIPE}='IGNORE'.
  dynamic "email_notifications" {
    for_each = length(var.finance_report_alert_emails) == 0 ? [] : [1]

    content {
      on_failure = var.finance_report_alert_emails
    }
  }
}

# Migration unit: run_all_orchestration (estate orchestration).
# Child-contributed job definition; applied only by the parent session.
#
# Replaces etl/legacy-extra/run_all.sh: one multi-task job wiring
# ingest -> parse -> finance through real task dependencies instead of
# sleep-based sequencing, with a final ALL_DONE recorder task that writes
# per-task outcomes to ow_tp.gold.estate_run_log. Each stage runs the
# sibling unit's job as-is (run_job_task) — nothing is redefined here.
#
# NOTE: the finance stage references databricks_job.finance_excel_report
# from the finance_excel_report unit's jobs_finance_excel_report.tf; the
# parent applies at the wave boundary after both units are merged.

resource "databricks_notebook" "estate_run_log" {
  path     = "/Shared/${var.prefix}/estate_run_log"
  language = "PYTHON"
  source   = "${path.module}/../../etl/databricks/run_all_orchestration/estate_run_log_notebook.py"
}

resource "databricks_job" "custbill_estate" {
  name        = "${var.prefix}_custbill_estate"
  description = "CUSTBILL estate orchestration: ingest -> parse -> finance via task dependencies (replaces run_all.sh sleep sequencing); records per-task outcomes to ${var.prefix}.gold.estate_run_log"

  # Retires the crontab-overlap deficiency: overlapping triggers queue
  # instead of interleaving; only one estate run is ever active.
  max_concurrent_runs = 1

  queue {
    enabled = true
  }

  parameter {
    name    = "ns"
    default = "demo"
  }

  # Tasks are declared in alphabetical task_key order to match the order the
  # Jobs API returns them in, keeping `terraform plan` clean between applies.
  task {
    task_key = "finance"

    depends_on {
      task_key = "parse"
    }

    run_job_task {
      job_id = databricks_job.finance_excel_report.id
      job_parameters = {
        ns = "{{job.parameters.ns}}"
      }
    }
  }

  task {
    task_key = "ingest"

    run_job_task {
      job_id = databricks_job.sftp_ingest_poll.id
      job_parameters = {
        ns = "{{job.parameters.ns}}"
      }
    }
  }

  task {
    task_key = "parse"

    depends_on {
      task_key = "ingest"
    }

    run_job_task {
      job_id = databricks_job.parse_custbill_fixedwidth.id
      job_parameters = {
        ns = "{{job.parameters.ns}}"
      }
    }
  }

  # Runs regardless of upstream outcome so every estate run gets an
  # attributed log row per task; re-raises on any non-success upstream
  # state so a failed stage is never a green run over partial data.
  task {
    task_key = "record_run_log"
    run_if   = "ALL_DONE"

    depends_on {
      task_key = "finance"
    }
    depends_on {
      task_key = "ingest"
    }
    depends_on {
      task_key = "parse"
    }

    notebook_task {
      notebook_path = databricks_notebook.estate_run_log.path
      base_parameters = {
        ns             = "{{job.parameters.ns}}"
        estate_run_id  = "{{job.run_id}}"
        job_id         = "{{job.id}}"
        ingest_result  = "{{tasks.ingest.result_state}}"
        parse_result   = "{{tasks.parse.result_state}}"
        finance_result = "{{tasks.finance.result_state}}"
      }
    }
  }
}

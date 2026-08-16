# Job definition for the finance_excel_report migration unit.
#
# Converts etl/legacy-extra/jobs/finance_excel_report.pl into a serverless
# notebook-task job. The notebook source lives at
# etl/databricks/finance_excel_report/finance_excel_report.py and is imported
# to /Shared/ow_tp/finance_excel_report by the parent (dbx.py import-notebook)
# before the job runs.
#
# Contributed as a child-owned jobs_<unit>.tf per the shared-stack ownership
# rules; only the parent session applies it. No clusters and nothing with an
# hourly cost: the task runs on serverless compute.

resource "databricks_job" "finance_excel_report" {
  name        = "${var.prefix}_finance_excel_report"
  description = "Aggregate ow_tp.silver.custbill_records into ow_tp.gold.finance_billing_summary and write the deterministic CSV export to the landing volume exports path (per-batch trigger granularity, idempotent per (ns, report_date) run key)"

  parameter {
    name    = "ns"
    default = "demo"
  }

  parameter {
    name    = "report_date"
    default = ""
  }

  task {
    task_key = "finance_excel_report"

    notebook_task {
      notebook_path = "/Shared/${var.prefix}/finance_excel_report"
      base_parameters = {
        ns          = "{{job.parameters.ns}}"
        report_date = "{{job.parameters.report_date}}"
      }
    }
  }

  queue {
    enabled = true
  }
}

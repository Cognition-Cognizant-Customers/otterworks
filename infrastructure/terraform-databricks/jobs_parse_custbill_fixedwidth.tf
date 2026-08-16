# Job definition for the parse_custbill_fixedwidth migration unit.
#
# Converts etl/legacy-extra/jobs/parse_custbill_fixedwidth.sh into a
# serverless notebook-task job. The notebook source lives at
# etl/databricks/parse_custbill_fixedwidth/parse_custbill_fixedwidth.py and is
# imported to /Shared/ow_tp/parse_custbill_fixedwidth by the parent
# (dbx.py import-notebook) before the job runs.
#
# Contributed as a child-owned jobs_<unit>.tf per the shared-stack ownership
# rules; only the parent session applies it. No clusters and nothing with an
# hourly cost: the task runs on serverless compute.

resource "databricks_job" "parse_custbill_fixedwidth" {
  name        = "${var.prefix}_parse_custbill_fixedwidth"
  description = "Parse CUSTBILL fixed-width drops from the bronze landing volume into ow_tp.silver.custbill_records / custbill_rescue (per-file trigger granularity, idempotent reruns)"

  parameter {
    name    = "ns"
    default = "demo"
  }

  task {
    task_key = "parse_custbill_fixedwidth"

    notebook_task {
      notebook_path = "/Shared/${var.prefix}/parse_custbill_fixedwidth"
      base_parameters = {
        ns = "{{job.parameters.ns}}"
      }
    }
  }

  queue {
    enabled = true
  }
}

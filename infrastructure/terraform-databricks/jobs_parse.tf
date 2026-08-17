# The shared Databricks stack is parent-owned; this unit file is never applied.
# It documents the runtime-created, manually triggered serverless job.

variable "ns" {
  type    = string
  default = "cnvparse"
}

variable "catalog" {
  type    = string
  default = "ow_tp"
}

resource "databricks_job" "parse_custbill" {
  name                = "ow_tp_parse_${var.ns}"
  max_concurrent_runs = 1

  # No schedule: demo-day runs are triggered manually.
  task {
    task_key = "parse_custbill"

    notebook_task {
      notebook_path = "/Shared/ow_tp/parse_custbill_${var.ns}"
      base_parameters = {
        ns      = var.ns
        catalog = var.catalog
      }
    }
  }

  tags = {
    project = "otterworks-tp"
    unit    = "dbx-parse"
    ns      = var.ns
  }
}

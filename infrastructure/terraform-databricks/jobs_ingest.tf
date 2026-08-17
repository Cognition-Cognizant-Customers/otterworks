# Parent-owned provider/backend configuration is intentionally not repeated here.
terraform {
  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "= 1.126.0"
    }
  }
}

variable "ow_tp_ingest_catalog" {
  description = "Parent-owned Unity Catalog catalog."
  type        = string
  default     = "ow_tp"
}

variable "ow_tp_ingest_ns" {
  description = "Namespace suffix for the ingest job and its notebook."
  type        = string
  default     = "cnvingest"
}

variable "ow_tp_ingest_notebook_path" {
  description = "Optional workspace path override for the namespace ingest notebook."
  type        = string
  default     = ""
}

locals {
  ow_tp_ingest_notebook_path = coalesce(
    var.ow_tp_ingest_notebook_path != "" ? var.ow_tp_ingest_notebook_path : null,
    "/Shared/ow_tp/ingest_${var.ow_tp_ingest_ns}",
  )
}

resource "databricks_job" "ow_tp_ingest" {
  name                = "ow_tp_ingest_${var.ow_tp_ingest_ns}"
  max_concurrent_runs = 1
  timeout_seconds     = 900

  tags = {
    project   = "otterworks-tp"
    unit      = "dbx-ingest"
    namespace = var.ow_tp_ingest_ns
  }

  # Serverless is intentional: no cluster or job_cluster block is declared.
  queue {
    enabled = true
  }

  # No schedule is declared; this job is manual-trigger only.
  task {
    task_key = "ingest"

    notebook_task {
      notebook_path = local.ow_tp_ingest_notebook_path
      base_parameters = {
        ns      = var.ow_tp_ingest_ns
        catalog = var.ow_tp_ingest_catalog
        run_id  = "{{job.run_id}}"
      }
    }
  }
}

# The parent session owns shared state; this file contributes only this unit's job.

variable "ns" {
  type    = string
  default = "cnvfinance"
}

variable "catalog" {
  type    = string
  default = "ow_tp"
}

resource "databricks_job" "finance" {
  name                = "ow_tp_finance_${var.ns}"
  max_concurrent_runs = 1

  tags = {
    project   = "otterworks-tp"
    demo      = "custbill-finance"
    namespace = var.ns
  }

  parameter {
    name    = "ns"
    default = var.ns
  }

  parameter {
    name    = "catalog"
    default = var.catalog
  }

  parameter {
    name    = "input_subdir"
    default = "parsed"
  }

  parameter {
    name    = "export_name"
    default = "finance_billing.csv"
  }

  parameter {
    name    = "delivery_probe"
    default = "off"
  }

  task {
    task_key = "finance_billing"

    notebook_task {
      notebook_path = "/Shared/ow_tp/finance_billing_${var.ns}"

      base_parameters = {
        ns             = "{{job.parameters.ns}}"
        catalog        = "{{job.parameters.catalog}}"
        input_subdir   = "{{job.parameters.input_subdir}}"
        export_name    = "{{job.parameters.export_name}}"
        delivery_probe = "{{job.parameters.delivery_probe}}"
        run_id         = "{{job.run_id}}"
      }
    }
  }

  schedule {
    quartz_cron_expression = "0 0 7 * * ?"
    timezone_id            = "UTC"
    pause_status           = "PAUSED"
  }

  queue {
    enabled = true
  }
}

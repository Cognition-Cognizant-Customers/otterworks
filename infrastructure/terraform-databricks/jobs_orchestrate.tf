terraform {
  required_version = ">= 1.7.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.95"
    }
  }
}

provider "databricks" {}

variable "namespace" {
  description = "Databricks migration namespace for the orchestrate job"
  type        = string
  default     = "cnvorch"
}

variable "sql_warehouse_id" {
  description = "Id of an existing parent-owned serverless SQL warehouse; this unit never creates compute. No default: an apply must name the warehouse of the workspace it targets rather than silently binding to one."
  type        = string
}

data "databricks_sql_warehouse" "shared_serverless" {
  id = var.sql_warehouse_id
}

resource "databricks_job" "orchestrate" {
  name                = "ow_tp_custbill_chain_${var.namespace}"
  description         = "CUSTBILL chain: ingest -> parse -> finance with declared task dependencies, replacing etl/legacy-extra/crontab offsets and run_all.sh's sleep-based 'dependency management'."
  max_concurrent_runs = 1

  queue {
    enabled = true
  }

  parameter {
    name    = "ns"
    default = var.namespace
  }

  email_notifications {
    on_failure                = ["ow-tp-alerts@otterworks.example.com"]
    no_alert_for_skipped_runs = false
  }

  schedule {
    quartz_cron_expression = "0 10 2 * * ?"
    timezone_id            = "UTC"
    pause_status           = "PAUSED"
  }

  tags = {
    project   = "otterworks-tp"
    unit      = "dbx-orchestrate"
    namespace = var.namespace
  }

  task {
    task_key = "validate_params"

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.shared_serverless.id
      parameters = {
        ns     = "{{job.parameters.ns}}"
        run_id = "{{job.run_id}}"
      }

      file {
        path   = "/Shared/ow_tp/chain_${var.namespace}/validate_params.sql"
        source = "WORKSPACE"
      }
    }

    max_retries               = 2
    min_retry_interval_millis = 15000
    retry_on_timeout          = false
    timeout_seconds           = 1800
  }

  task {
    task_key = "ingest"

    depends_on {
      task_key = "validate_params"
    }

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.shared_serverless.id
      parameters = {
        ns     = "{{job.parameters.ns}}"
        run_id = "{{job.run_id}}"
      }

      file {
        path   = "/Shared/ow_tp/chain_${var.namespace}/ingest.sql"
        source = "WORKSPACE"
      }
    }

    max_retries               = 2
    min_retry_interval_millis = 15000
    retry_on_timeout          = false
    timeout_seconds           = 1800
  }

  task {
    task_key = "parse"

    depends_on {
      task_key = "ingest"
    }

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.shared_serverless.id
      parameters = {
        ns     = "{{job.parameters.ns}}"
        run_id = "{{job.run_id}}"
      }

      file {
        path   = "/Shared/ow_tp/chain_${var.namespace}/parse.sql"
        source = "WORKSPACE"
      }
    }

    max_retries               = 2
    min_retry_interval_millis = 15000
    retry_on_timeout          = false
    timeout_seconds           = 1800
  }

  task {
    task_key = "finance"

    depends_on {
      task_key = "parse"
    }

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.shared_serverless.id
      parameters = {
        ns     = "{{job.parameters.ns}}"
        run_id = "{{job.run_id}}"
      }

      file {
        path   = "/Shared/ow_tp/chain_${var.namespace}/finance.sql"
        source = "WORKSPACE"
      }
    }

    max_retries               = 2
    min_retry_interval_millis = 15000
    retry_on_timeout          = false
    timeout_seconds           = 1800
  }

  task {
    task_key = "chain_complete"

    depends_on {
      task_key = "finance"
    }

    run_if = "ALL_SUCCESS"

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.shared_serverless.id
      parameters = {
        ns     = "{{job.parameters.ns}}"
        run_id = "{{job.run_id}}"
      }

      file {
        path   = "/Shared/ow_tp/chain_${var.namespace}/chain_complete.sql"
        source = "WORKSPACE"
      }
    }

    max_retries               = 2
    min_retry_interval_millis = 15000
    retry_on_timeout          = false
    timeout_seconds           = 1800
  }

  task {
    task_key = "chain_failed"

    depends_on {
      task_key = "validate_params"
    }

    depends_on {
      task_key = "ingest"
    }

    depends_on {
      task_key = "parse"
    }

    depends_on {
      task_key = "finance"
    }

    run_if = "AT_LEAST_ONE_FAILED"

    sql_task {
      warehouse_id = data.databricks_sql_warehouse.shared_serverless.id
      parameters = {
        ns     = "{{job.parameters.ns}}"
        run_id = "{{job.run_id}}"
      }

      file {
        path   = "/Shared/ow_tp/chain_${var.namespace}/chain_failed.sql"
        source = "WORKSPACE"
      }
    }

    max_retries               = 0
    min_retry_interval_millis = 15000
    retry_on_timeout          = false
    timeout_seconds           = 1800
  }
}

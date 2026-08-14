locals {
  notebooks = {
    custbill_bronze_ingest = "Replaces sftp_ingest_poll.ksh"
    custbill_silver_parse  = "Replaces parse_custbill_fixedwidth.sh"
    custbill_gold_finance  = "Replaces finance_excel_report.pl"
    analytics_daily        = "Replaces etl/scripts/analytics_daily.py"
    audit_archive_weekly   = "Replaces etl/scripts/audit_archive_weekly.py"
    search_reindex_weekly  = "Replaces etl/scripts/search_reindex_weekly.py"
    storage_cleanup_daily  = "Replaces etl/scripts/storage_cleanup_daily.py"
    user_activity_daily    = "Replaces etl/scripts/user_activity_daily.py"
  }

  base_parameters = {
    catalog = var.catalog_name
    ns      = "{{job.parameters.ns}}"
  }
}

resource "databricks_directory" "pipeline_root" {
  path             = "/Shared/${var.prefix}"
  delete_recursive = true
}

resource "databricks_notebook" "pipeline" {
  for_each = local.notebooks

  path     = "${databricks_directory.pipeline_root.path}/${each.key}"
  source   = "${path.module}/../../databricks/notebooks/${each.key}.py"
  language = "PYTHON"
}

# CUSTBILL mainframe-feed pipeline: the run_all.sh cron chain (ingest -> parse
# -> finance report) becomes dependency-ordered serverless tasks — no `sleep`,
# no lock files, retries and single-flight handled by the platform.
resource "databricks_job" "custbill_lakehouse" {
  name                = "${var.prefix}_custbill_lakehouse"
  max_concurrent_runs = 1

  parameter {
    name    = "ns"
    default = "dev"
  }

  task {
    task_key = "bronze_ingest"
    notebook_task {
      notebook_path   = databricks_notebook.pipeline["custbill_bronze_ingest"].path
      base_parameters = local.base_parameters
    }
  }

  task {
    task_key = "silver_parse"
    depends_on {
      task_key = "bronze_ingest"
    }
    notebook_task {
      notebook_path   = databricks_notebook.pipeline["custbill_silver_parse"].path
      base_parameters = local.base_parameters
    }
  }

  task {
    task_key = "gold_finance"
    depends_on {
      task_key = "silver_parse"
    }
    notebook_task {
      notebook_path   = databricks_notebook.pipeline["custbill_gold_finance"].path
      base_parameters = local.base_parameters
    }
  }
}

# The five Python cron scripts become one dependency-aware workflow (cron had
# to guess ordering via start times: analytics at 02:00, cleanup 04:00, ...).
resource "databricks_job" "python_etl_wave" {
  name                = "${var.prefix}_python_etl_wave"
  max_concurrent_runs = 1

  parameter {
    name    = "ns"
    default = "dev"
  }

  task {
    task_key = "analytics_daily"
    notebook_task {
      notebook_path   = databricks_notebook.pipeline["analytics_daily"].path
      base_parameters = local.base_parameters
    }
  }

  task {
    task_key = "audit_archive_weekly"
    depends_on {
      task_key = "analytics_daily"
    }
    notebook_task {
      notebook_path   = databricks_notebook.pipeline["audit_archive_weekly"].path
      base_parameters = local.base_parameters
    }
  }

  task {
    task_key = "user_activity_daily"
    depends_on {
      task_key = "analytics_daily"
    }
    notebook_task {
      notebook_path   = databricks_notebook.pipeline["user_activity_daily"].path
      base_parameters = local.base_parameters
    }
  }

  task {
    task_key = "storage_cleanup_daily"
    notebook_task {
      notebook_path   = databricks_notebook.pipeline["storage_cleanup_daily"].path
      base_parameters = local.base_parameters
    }
  }

  task {
    task_key = "search_reindex_weekly"
    depends_on {
      task_key = "storage_cleanup_daily"
    }
    notebook_task {
      notebook_path   = databricks_notebook.pipeline["search_reindex_weekly"].path
      base_parameters = local.base_parameters
    }
  }
}

# Migration unit: sftp_ingest_poll (bronze ingest registration).
# Child-contributed job definition; applied only by the parent session.

resource "databricks_notebook" "sftp_ingest_poll" {
  path     = "/Shared/${var.prefix}/sftp_ingest_poll"
  language = "PYTHON"
  source   = "${path.module}/../../etl/databricks/sftp_ingest_poll/sftp_ingest_poll_notebook.py"
}

resource "databricks_job" "sftp_ingest_poll" {
  name = "${var.prefix}_sftp_ingest_poll"

  # Reruns are idempotent, but serialize runs per workspace to keep
  # per-file attribution unambiguous (legacy lock-file replacement).
  max_concurrent_runs = 1

  queue {
    enabled = true
  }

  parameter {
    name    = "ns"
    default = "demo"
  }

  parameter {
    name    = "volume_root"
    default = "/Volumes/${var.prefix}/bronze/landing"
  }

  parameter {
    name    = "catalog"
    default = var.prefix
  }

  parameter {
    name    = "schema"
    default = "bronze"
  }

  parameter {
    name    = "table"
    default = "custbill_raw_files"
  }

  # Serverless notebook task: no cluster block, no hourly-cost resources.
  task {
    task_key = "ingest"

    notebook_task {
      notebook_path = databricks_notebook.sftp_ingest_poll.path
    }
  }
}

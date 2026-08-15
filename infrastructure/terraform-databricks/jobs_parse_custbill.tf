# The legacy parser ran on a cron offset from ingest and could read a
# half-written file.  This job gates on the bronze manifest, serializes runs,
# and leaves scheduling paused until the orchestrator owns the chain.
variable "parse_custbill_chain_from_ingest" {
  description = "When true, wait for the optional ow_tp_sftp_ingest job before checking the bronze manifest."
  type        = bool
  default     = false
}

data "databricks_job" "sftp_ingest" {
  count = var.parse_custbill_chain_from_ingest ? 1 : 0
  name  = "ow_tp_sftp_ingest"
}

resource "databricks_job" "parse_custbill" {
  name                = "ow_tp_parse_custbill"
  description         = "Parse CUSTBILL fixed-width files for the demo namespace; bronze input is manifest-gated."
  max_concurrent_runs = 1
  timeout_seconds     = 1800

  # The legacy cron ran every 15 minutes at :05, racing the :00 ingest cron.
  # Keep the schedule paused: orchestration and the manifest gate, not a wall
  # clock offset, sequence the chain.
  schedule {
    quartz_cron_expression = "0 5/15 * * * ?"
    timezone_id            = "UTC"
    pause_status           = "PAUSED"
  }

  # Job parameters replace the legacy host-selected paths with explicit,
  # repeatable per-namespace state.
  parameter {
    name    = "ns"
    default = "demo"
  }

  # Demo state is per run and per namespace: the namespace is a run parameter,
  # not a property of the job, so it is deliberately not a tag.
  tags = {
    demo     = "tech-partnerships"
    pipeline = "custbill"
    layer    = "silver"
  }

  # Optional chaining is disabled by default so this unit remains plan-safe
  # before the sibling ingest job exists in the workspace.
  dynamic "task" {
    for_each = var.parse_custbill_chain_from_ingest ? [1] : []
    content {
      task_key = "upstream_ingest"

      run_job_task {
        job_id = data.databricks_job.sftp_ingest[0].id
      }
    }
  }

  task {
    task_key = "wait_for_bronze_manifest"

    dynamic "depends_on" {
      for_each = var.parse_custbill_chain_from_ingest ? [1] : []
      content {
        task_key = "upstream_ingest"
      }
    }

    # Reusing the parser notebook in gate mode retires the legacy implicit
    # file handoff without introducing a second pipeline implementation.
    notebook_task {
      notebook_path = "/Shared/ow_tp/parse_custbill_fixedwidth"
      base_parameters = {
        ns   = "{{job.parameters.ns}}"
        mode = "gate"
      }
    }
  }

  task {
    task_key = "parse"

    # Parsing cannot start until the bronze manifest gate succeeds.
    depends_on {
      task_key = "wait_for_bronze_manifest"
    }

    # A notebook task with no cluster block uses serverless job compute; this
    # retires the legacy host-specific, implicitly provisioned execution path.
    notebook_task {
      notebook_path = "/Shared/ow_tp/parse_custbill_fixedwidth"
      base_parameters = {
        ns   = "{{job.parameters.ns}}"
        mode = "parse"
      }
    }
  }

  # The shared foundation owns /Shared/ow_tp; ensure it exists before the job
  # references the deployed notebook beneath it.
  depends_on = [databricks_directory.pipelines]
}

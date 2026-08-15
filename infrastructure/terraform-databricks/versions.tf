terraform {
  required_version = ">= 1.5.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.50"
    }
  }
}

# Host/token come from DATABRICKS_HOST / DATABRICKS_TOKEN in the environment
# (the demo maps DATABRICKS_DEMO_HOST / DATABRICKS_DEMO_TOKEN onto them via
# the dbx-* Make targets) so no credential ever lands in code or state config.
provider "databricks" {}

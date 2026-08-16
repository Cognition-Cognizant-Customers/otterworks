terraform {
  required_version = ">= 1.6.0"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.68"
    }
  }
}

# Auth comes from DATABRICKS_HOST / DATABRICKS_TOKEN in the environment.
# The parent orchestration session is the only holder of this state.
provider "databricks" {}

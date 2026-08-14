terraform {
  required_version = ">= 1.5"

  required_providers {
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.60"
    }
  }
}

# Auth comes from the environment — never hardcode host or token:
#   export DATABRICKS_HOST="$DATABRICKS_DEMO_HOST"
#   export DATABRICKS_TOKEN="$DATABRICKS_DEMO_TOKEN"
provider "databricks" {}

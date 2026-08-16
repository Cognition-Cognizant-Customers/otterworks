# Shared, parent-owned Databricks estate for the tech-partnerships migration demo.
#
# Everything here carries the demo prefix and is applied/destroyed ONLY by the
# parent orchestration session. Children contribute job definitions as
# jobs_<unit>.tf files but never hold or apply this state, and never run DDL
# against shared tables.

data "databricks_sql_warehouse" "serverless" {
  name = var.warehouse_name
}

# The catalog itself is created/dropped by catalog.sh (SQL CREATE CATALOG):
# this workspace has Default Storage enabled and rejects catalog creation via
# the Unity Catalog API, while the SQL path succeeds. catalog.sh is part of
# the parent-owned shared stack.

resource "databricks_schema" "medallion" {
  for_each = toset(["bronze", "silver", "gold"])

  catalog_name  = var.prefix
  name          = each.key
  comment       = "Medallion layer '${each.key}' for the ${var.prefix} demo"
  force_destroy = true
}

resource "databricks_volume" "landing" {
  catalog_name = var.prefix
  schema_name  = databricks_schema.medallion["bronze"].name
  name         = "landing"
  volume_type  = "MANAGED"
  comment      = "Landing volume for raw legacy batch drops (Files API transport)"
}

resource "databricks_secret_scope" "ow_tp" {
  name = var.prefix
}

resource "databricks_directory" "shared_root" {
  path = "/Shared/${var.prefix}"
}

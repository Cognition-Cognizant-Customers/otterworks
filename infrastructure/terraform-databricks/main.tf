# Shared foundation for the tech-partnerships Databricks migration track: the
# lakehouse layers the converted ETL jobs land in, plus the secret scope that
# replaces the legacy estate's hardcoded credentials.
#
# Per-unit job definitions live in jobs_<unit>.tf, contributed by the
# conversion work units and applied from here so the whole estate is one
# `terraform apply` / `terraform destroy` unit.

data "databricks_sql_warehouse" "serverless" {
  name = var.warehouse_name
}

# This workspace has Default Storage enabled, and the Unity Catalog REST API
# refuses to create a catalog without an explicit MANAGED LOCATION there --
# `CREATE CATALOG` over the SQL Statement Execution API is the supported path,
# so the catalog is managed as a scripted resource instead of
# databricks_catalog. Destroy drops it CASCADE, which also removes tables the
# pipelines created outside Terraform's view.
resource "terraform_data" "catalog" {
  triggers_replace = [var.catalog_name]

  input = {
    catalog      = var.catalog_name
    warehouse_id = data.databricks_sql_warehouse.serverless.id
  }

  provisioner "local-exec" {
    command = "\"${path.module}/catalog.sh\" create \"${self.input.warehouse_id}\" \"${self.input.catalog}\""
  }

  provisioner "local-exec" {
    when    = destroy
    command = "\"${path.module}/catalog.sh\" drop \"${self.input.warehouse_id}\" \"${self.input.catalog}\""
  }
}

locals {
  layers = {
    bronze = "Raw landings: CUSTBILL fixed-width files and the event/metadata extracts the Python crons pulled from SQS, DynamoDB and S3."
    silver = "Typed, schema-validated records plus quarantine and file-audit tables."
    gold   = "Business aggregates that replace the legacy CSV/'xls' reports."
  }
}

resource "databricks_schema" "layer" {
  for_each = local.layers

  depends_on    = [terraform_data.catalog]
  catalog_name  = var.catalog_name
  name          = each.key
  comment       = each.value
  force_destroy = true
}

# Replaces the SFTP drop directory and the per-host /data/otterworks trees the
# legacy jobs selected with hostname if-blocks.
resource "databricks_volume" "landing" {
  catalog_name = var.catalog_name
  schema_name  = databricks_schema.layer["bronze"].name
  name         = "landing"
  volume_type  = "MANAGED"
  comment      = "Upload area, namespaced per demo run: <ns>/custbill/*.dat and <ns>/<source>/ extracts."
}

resource "databricks_secret_scope" "estate" {
  name = var.prefix
}

resource "databricks_secret" "estate" {
  # Key names are not sensitive; only the values are, so iterate the keys.
  for_each = toset(nonsensitive(keys(var.secrets)))

  scope        = databricks_secret_scope.estate.name
  key          = each.key
  string_value = var.secrets[each.key]
}

# Workspace home for the converted pipelines. Job definitions reference
# notebooks under this path; delete_recursive keeps teardown complete.
resource "databricks_directory" "pipelines" {
  path             = "/Shared/${var.prefix}"
  delete_recursive = true
}

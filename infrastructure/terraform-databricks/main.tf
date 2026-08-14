# OtterWorks tech-partnerships Databricks lakehouse estate.
# Everything here is namespaced ow_tp_* / catalog ow_tp so `terraform destroy`
# removes the whole demo without touching anything else in the shared workspace.

data "databricks_sql_warehouse" "existing" {
  name = var.warehouse_name
}

# This workspace has Default Storage enabled: the Unity Catalog REST API
# refuses to create catalogs ("Please use the UI..."), but SQL CREATE CATALOG
# on the serverless warehouse works. The catalog is therefore managed via the
# SQL Statement Execution API; destroy runs DROP CATALOG ... CASCADE so
# teardown still removes everything, including tables created by pipeline runs.
resource "terraform_data" "catalog" {
  triggers_replace = [var.catalog_name]

  input = {
    catalog      = var.catalog_name
    warehouse_id = data.databricks_sql_warehouse.existing.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      curl -sS --fail-with-body -X POST "$DATABRICKS_HOST/api/2.0/sql/statements" \
        -H "Authorization: Bearer $DATABRICKS_TOKEN" -H "Content-Type: application/json" \
        -d '${jsonencode({
    warehouse_id    = data.databricks_sql_warehouse.existing.id
    statement       = "CREATE CATALOG IF NOT EXISTS `${var.catalog_name}`"
    wait_timeout    = "50s"
    on_wait_timeout = "CANCEL"
})}' | grep -q '"SUCCEEDED"'
    EOT
}

provisioner "local-exec" {
  when    = destroy
  command = <<-EOT
      curl -sS --fail-with-body -X POST "$DATABRICKS_HOST/api/2.0/sql/statements" \
        -H "Authorization: Bearer $DATABRICKS_TOKEN" -H "Content-Type: application/json" \
        -d '{"warehouse_id":"${self.input.warehouse_id}","statement":"DROP CATALOG IF EXISTS `${self.input.catalog}` CASCADE","wait_timeout":"50s","on_wait_timeout":"CANCEL"}' | grep -q '"SUCCEEDED"'
    EOT
}
}

resource "databricks_schema" "bronze" {
  depends_on    = [terraform_data.catalog]
  catalog_name  = var.catalog_name
  name          = "bronze"
  comment       = "Raw landed CUSTBILL files, event stream, and metadata exports."
  force_destroy = true
}

resource "databricks_schema" "silver" {
  depends_on    = [terraform_data.catalog]
  catalog_name  = var.catalog_name
  name          = "silver"
  comment       = "Typed, validated records with quarantine + file audits."
  force_destroy = true
}

resource "databricks_schema" "gold" {
  depends_on    = [terraform_data.catalog]
  catalog_name  = var.catalog_name
  name          = "gold"
  comment       = "Business aggregates replacing the legacy cron reports."
  force_destroy = true
}

# Landing zone replacing the legacy SFTP drop + local cron box directories.
resource "databricks_volume" "landing" {
  catalog_name = var.catalog_name
  schema_name  = databricks_schema.bronze.name
  name         = "landing"
  volume_type  = "MANAGED"
  comment      = "Upload area: <ns>/custbill/*.dat, <ns>/events/, <ns>/file_metadata/, <ns>/documents/."
}

# Secret scope replacing the hardcoded credentials scattered across the
# legacy estate (etl/config.ini passwords, plaintext mvsprod in sftp_ingest_poll.ksh).
resource "databricks_secret_scope" "demo" {
  name = var.prefix
}

resource "databricks_secret" "secrets" {
  for_each = {
    pg_password           = var.pg_password
    aws_access_key_id     = var.aws_access_key_id
    aws_secret_access_key = var.aws_secret_access_key
    meilisearch_api_key   = var.meilisearch_api_key
    sftp_password         = var.sftp_password
  }

  scope        = databricks_secret_scope.demo.name
  key          = each.key
  string_value = each.value
}

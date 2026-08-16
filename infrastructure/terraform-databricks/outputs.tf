output "catalog" {
  value = var.prefix
}

output "schemas" {
  value = [for s in databricks_schema.medallion : "${s.catalog_name}.${s.name}"]
}

output "landing_volume_path" {
  value = "/Volumes/${var.prefix}/${databricks_schema.medallion["bronze"].name}/${databricks_volume.landing.name}"
}

output "warehouse_id" {
  value = data.databricks_sql_warehouse.serverless.id
}

output "secret_scope" {
  value = databricks_secret_scope.ow_tp.name
}

output "notebook_root" {
  value = databricks_directory.shared_root.path
}

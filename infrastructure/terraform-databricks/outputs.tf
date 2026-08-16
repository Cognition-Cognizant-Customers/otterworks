output "catalog" {
  description = "Catalog holding the lakehouse layers."
  value       = var.catalog_name
}

output "schemas" {
  description = "Fully qualified bronze/silver/gold schema names."
  value       = { for k, s in databricks_schema.layer : k => "${var.catalog_name}.${s.name}" }
}

output "landing_volume" {
  description = "Volume path the local drivers upload legacy inputs to."
  value       = "/Volumes/${var.catalog_name}/${databricks_schema.layer["bronze"].name}/${databricks_volume.landing.name}"
}

output "warehouse_id" {
  description = "Existing serverless SQL warehouse the jobs and recon queries run on."
  value       = data.databricks_sql_warehouse.serverless.id
}

output "secret_scope" {
  description = "Secret scope replacing the legacy hardcoded credentials."
  value       = databricks_secret_scope.estate.name
}

output "pipeline_root" {
  description = "Workspace directory holding the converted pipeline notebooks."
  value       = databricks_directory.pipelines.path
}

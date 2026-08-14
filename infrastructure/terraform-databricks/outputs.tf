output "catalog" {
  value = var.catalog_name
}

output "landing_volume" {
  value = "/Volumes/${var.catalog_name}/${databricks_schema.bronze.name}/${databricks_volume.landing.name}"
}

output "warehouse_id" {
  description = "Existing serverless SQL warehouse reused for queries (not managed by this stack)."
  value       = data.databricks_sql_warehouse.existing.id
}

output "custbill_job_id" {
  value = databricks_job.custbill_lakehouse.id
}

output "python_etl_wave_job_id" {
  value = databricks_job.python_etl_wave.id
}

output "secret_scope" {
  value = databricks_secret_scope.demo.name
}

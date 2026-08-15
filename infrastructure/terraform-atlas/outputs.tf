locals {
  active_cluster = var.manage_cluster ? mongodbatlas_advanced_cluster.managed[0] : data.mongodbatlas_advanced_cluster.existing[0]
}

output "srv_connection_string" {
  description = "Atlas SRV connection string without credentials."
  value       = local.active_cluster.connection_strings[0].standard_srv
}

output "database_name" {
  description = "Per-run migration database name."
  value       = local.database_name
}

output "migration_username" {
  description = "Per-run migration database username."
  value       = mongodbatlas_database_user.migrator.username
}

output "child_env" {
  description = "Connection summary for child migration sessions."
  value = {
    MONGODB_ATLAS_URI      = "${local.active_cluster.connection_strings[0].standard_srv}/${local.database_name}"
    MONGODB_ATLAS_SRV_HOST = local.active_cluster.connection_strings[0].standard_srv
    MONGODB_DATABASE       = local.database_name
    MONGODB_USERNAME       = mongodbatlas_database_user.migrator.username
  }
}

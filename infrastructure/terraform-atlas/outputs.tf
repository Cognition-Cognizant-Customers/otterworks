output "cluster_srv_address" {
  value       = data.mongodbatlas_advanced_cluster.shared.connection_strings[0].standard_srv
  description = "SRV connection string of the shared cluster (no credentials)."
}

output "run_database" {
  value = local.run_db
}

output "quarantine_database" {
  value = local.quarantine_db
}

output "run_user" {
  value = mongodbatlas_database_user.run.username
}

output "run_user_password" {
  value     = random_password.run_user.result
  sensitive = true
}

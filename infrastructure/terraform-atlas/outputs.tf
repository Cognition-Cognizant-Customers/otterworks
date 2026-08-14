output "cluster_name" {
  value = mongodbatlas_advanced_cluster.demo.name
}

output "cluster_srv_address" {
  description = "mongodb+srv connection seed for the cluster (no credentials)."
  value       = mongodbatlas_advanced_cluster.demo.connection_strings[0].standard_srv
}

output "demo_db_username" {
  value = mongodbatlas_database_user.demo_migrator.username
}

output "demo_db_password" {
  description = "Generated password for the demo user. Read with: terraform output -raw demo_db_password"
  value       = random_password.demo_user.result
  sensitive   = true
}

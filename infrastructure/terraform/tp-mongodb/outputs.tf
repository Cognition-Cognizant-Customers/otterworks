output "cluster_name" {
  description = "The existing shared cluster read by this configuration."
  value       = data.mongodbatlas_cluster.shared.name
}

output "database_name" {
  value = local.database_name
}

output "database_username" {
  value = mongodbatlas_database_user.namespace.username
}

output "database_password" {
  sensitive = true
  value     = mongodbatlas_database_user.namespace.password
}

output "caller_ip" {
  value = var.manage_caller_access_list ? mongodbatlas_project_ip_access_list.caller[0].ip_address : local.caller_ip
}

data "mongodbatlas_project" "shared" {
  project_id = var.project_id
}

data "mongodbatlas_cluster" "shared" {
  project_id = var.project_id
  name       = var.cluster_name
}

data "http" "caller_ip" {
  url = "https://api.ipify.org"
}

locals {
  database_name = "ow_tp_${lower(var.ns)}"
  username      = "ow-tp-${lower(var.ns)}"
  caller_ip     = var.caller_ip != null ? var.caller_ip : trimspace(data.http.caller_ip.response_body)
}

resource "random_password" "database_user" {
  length  = 32
  special = false
}

resource "mongodbatlas_database_user" "namespace" {
  project_id         = data.mongodbatlas_project.shared.id
  username           = local.username
  password           = random_password.database_user.result
  auth_database_name = "admin"

  roles {
    role_name     = "readWrite"
    database_name = local.database_name
  }
}

resource "mongodbatlas_project_ip_access_list" "caller" {
  count      = var.manage_caller_access_list ? 1 : 0
  project_id = data.mongodbatlas_project.shared.id
  ip_address = local.caller_ip
  comment    = "otterworks-tp track=mongodb namespace=${var.ns}"
}

# Parent-owned, run-scoped Atlas objects for the MongoDB migration track.
#
# The M0 cluster (otterworks-demo) and the otterworks-app user are
# pre-provisioned project infrastructure shared across demos: they are
# consumed as a data source and deliberately NOT managed here, so
# `terraform destroy` can never remove shared estate. Everything this
# stack creates is scoped to one run namespace and is destroyed at
# teardown: a run database user restricted to the run's databases, and
# (optionally) the parent VM's IP access-list entry.

data "mongodbatlas_advanced_cluster" "shared" {
  project_id = var.project_id
  name       = var.cluster_name
}

locals {
  run_db        = "ow_tp_mongodb_${var.namespace}"
  quarantine_db = "ow_tp_mongodb_${var.namespace}_quarantine"
  run_user      = "ow_tp_mongodb_${var.namespace}"
}

resource "random_password" "run_user" {
  length  = 32
  special = false
}

resource "mongodbatlas_database_user" "run" {
  project_id         = var.project_id
  username           = local.run_user
  password           = random_password.run_user.result
  auth_database_name = "admin"

  roles {
    role_name     = "readWrite"
    database_name = local.run_db
  }

  roles {
    role_name     = "readWrite"
    database_name = local.quarantine_db
  }

  roles {
    role_name     = "dbAdmin"
    database_name = local.run_db
  }

  roles {
    role_name     = "dbAdmin"
    database_name = local.quarantine_db
  }

  scopes {
    name = var.cluster_name
    type = "CLUSTER"
  }
}

resource "mongodbatlas_project_ip_access_list" "parent_vm" {
  count      = var.vm_ip == "" ? 0 : 1
  project_id = var.project_id
  ip_address = var.vm_ip
  comment    = "ow-tp mongodb run ${var.namespace}: parent session VM"
}

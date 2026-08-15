locals {
  database_name  = "ow_tp_${var.ns}"
  username       = "ow_tp_${var.ns}_migrator"
  access_comment = "ow-tp-mongo-${var.ns}"
}

# The demo uses an existing shared M0 cluster. Atlas rejects updates to M0/M2/M5
# tenant clusters, and replacing this cluster would change its SRV hostname.
# For the full-scale path, manage_cluster=true creates a dedicated cluster whose
# tier is controlled by cluster_tier.
data "mongodbatlas_advanced_cluster" "existing" {
  count      = var.manage_cluster ? 0 : 1
  project_id = var.project_id
  name       = var.cluster_name
}

resource "mongodbatlas_advanced_cluster" "managed" {
  count                  = var.manage_cluster ? 1 : 0
  project_id             = var.project_id
  name                   = var.cluster_name
  cluster_type           = "REPLICASET"
  mongo_db_major_version = "8.0"

  replication_specs {
    zone_name = "Zone 1"

    region_configs {
      electable_specs {
        instance_size = var.cluster_tier
        node_count    = contains(["M0", "M2", "M5"], var.cluster_tier) ? null : 3
      }

      provider_name         = contains(["M0", "M2", "M5"], var.cluster_tier) ? "TENANT" : "AWS"
      backing_provider_name = contains(["M0", "M2", "M5"], var.cluster_tier) ? "AWS" : null
      region_name           = var.region
      priority              = 7
    }
  }
}

resource "mongodbatlas_database_user" "migrator" {
  project_id         = var.project_id
  username           = local.username
  password           = var.db_password
  auth_database_name = "admin"

  roles {
    role_name     = "readWrite"
    database_name = local.database_name
  }

  scopes {
    name = var.cluster_name
    type = "CLUSTER"
  }
}

resource "mongodbatlas_project_ip_access_list" "migration_vm" {
  project_id = var.project_id
  cidr_block = var.access_cidr
  comment    = local.access_comment
}

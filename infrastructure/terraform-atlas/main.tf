# Demo-grade Atlas configuration for the MongoDB modernization track.
# Everything Atlas-side is Terraform-managed so teardown is one command
# (see README.md — the shared M0 cluster needs `terraform state rm` first).

# IP access list — demo-grade open entry so Devin VMs / demo laptops
# (no stable egress IPs) can reach the cluster.
resource "mongodbatlas_project_ip_access_list" "demo_open" {
  project_id = var.project_id
  cidr_block = var.access_cidr
  comment    = "terraform-managed demo access (otterworks tech-partnerships)"
}

# Dedicated database user for the migration demo, scoped to readWriteAnyDatabase
# so it can create/drop the per-namespace ow_tp_<ns> databases.
resource "random_password" "demo_user" {
  length  = 24
  special = false
}

resource "mongodbatlas_database_user" "demo_migrator" {
  project_id         = var.project_id
  username           = var.demo_db_username
  password           = random_password.demo_user.result
  auth_database_name = "admin"

  roles {
    role_name     = "readWriteAnyDatabase"
    database_name = "admin"
  }

  scopes {
    name = var.cluster_name
    type = "CLUSTER"
  }
}

# The shared M0 cluster. IMPORTED into state (see README.md) — Atlas allows a
# single M0 per project, so this resource must never be created fresh here.
resource "mongodbatlas_advanced_cluster" "demo" {
  project_id             = var.project_id
  name                   = var.cluster_name
  cluster_type           = "REPLICASET"
  mongo_db_major_version = var.mongodb_major_version

  replication_specs {
    zone_name = "Zone 1" # matches the imported cluster so the plan is a no-op
    region_configs {
      electable_specs {
        instance_size = var.cluster_instance_size
      }
      # M0/M2/M5 shared tiers run on the TENANT provider backed by AWS.
      provider_name         = var.cluster_instance_size == "M0" ? "TENANT" : "AWS"
      backing_provider_name = var.cluster_instance_size == "M0" ? "AWS" : null
      region_name           = var.cluster_region
      priority              = 7
    }
  }

  lifecycle {
    # Belt-and-braces: the shared cluster must never be destroyed by this
    # stack. Remove it from state (terraform state rm) before any destroy.
    prevent_destroy = true
  }
}

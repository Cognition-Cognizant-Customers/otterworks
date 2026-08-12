# ------------------------------------------------------------------------------
# OtterWorks OpenSearch Module
# Amazon OpenSearch Serverless SEARCH collection for the search-service
# (managed replacement for the self-managed MeiliSearch module — deployed
# alongside it, never replacing it; namespaced so concurrent runs don't collide)
# ------------------------------------------------------------------------------

locals {
  collection_name = "${var.project}-search-${var.namespace}"
  common_tags = {
    Module    = "opensearch"
    Project   = var.project
    Namespace = var.namespace
  }
}

# --- Encryption policy (required before the collection can be created) ---

resource "aws_opensearchserverless_security_policy" "encryption" {
  name = "${local.collection_name}-enc"
  type = "encryption"
  policy = jsonencode({
    Rules = [
      {
        ResourceType = "collection"
        Resource     = ["collection/${local.collection_name}"]
      }
    ]
    AWSOwnedKey = true
  })
}

# --- Network policy ---

resource "aws_opensearchserverless_security_policy" "network" {
  name = "${local.collection_name}-net"
  type = "network"

  lifecycle {
    precondition {
      # AOSS rejects a non-public network rule with an empty SourceVPCEs list,
      # and the collection would be unreachable anyway.
      condition     = var.allow_public_access || length(var.vpc_endpoint_ids) > 0
      error_message = "vpc_endpoint_ids must be non-empty when allow_public_access = false (create an aws_opensearchserverless_vpc_endpoint and pass its ID)."
    }
  }

  policy = jsonencode([
    merge(
      {
        Rules = [
          {
            ResourceType = "collection"
            Resource     = ["collection/${local.collection_name}"]
          },
          {
            ResourceType = "dashboard"
            Resource     = ["collection/${local.collection_name}"]
          }
        ]
        AllowFromPublic = var.allow_public_access
      },
      # SourceVPCEs must be omitted entirely when public access is allowed.
      var.allow_public_access ? {} : { SourceVPCEs = var.vpc_endpoint_ids }
    )
  ])
}

# --- Collection ---

resource "aws_opensearchserverless_collection" "search" {
  name = local.collection_name
  type = "SEARCH"

  standby_replicas = var.standby_replicas

  lifecycle {
    precondition {
      # AOSS collection/policy names are limited to 32 chars; the longest
      # derived name appends "-data" to the collection name.
      condition     = length(local.collection_name) + 5 <= 32
      error_message = "Derived AOSS names exceed 32 characters; shorten var.project or var.namespace."
    }
  }

  tags = merge(local.common_tags, {
    Service = "search-service"
  })

  depends_on = [aws_opensearchserverless_security_policy.encryption]
}

# --- Data access policy ---

resource "aws_opensearchserverless_access_policy" "data_access" {
  name = "${local.collection_name}-data"
  type = "data"
  policy = jsonencode([
    {
      Rules = [
        {
          ResourceType = "collection"
          Resource     = ["collection/${local.collection_name}"]
          Permission = [
            "aoss:CreateCollectionItems",
            "aoss:DescribeCollectionItems",
          ]
        },
        {
          ResourceType = "index"
          Resource     = ["index/${local.collection_name}/*"]
          Permission = [
            "aoss:CreateIndex",
            "aoss:DeleteIndex",
            "aoss:UpdateIndex",
            "aoss:DescribeIndex",
            "aoss:ReadDocument",
            "aoss:WriteDocument",
          ]
        }
      ]
      Principal = var.access_principal_arns
    }
  ])
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = "otterworks-tp"
    }
  }
}

locals {
  name_prefix = "ow-tp-"
}

resource "aws_s3_bucket" "file_storage" {
  bucket = "${local.name_prefix}file-storage"
}

resource "aws_s3_bucket" "file_quarantine" {
  bucket = "${local.name_prefix}file-quarantine"
}

resource "aws_s3_bucket" "audit_archive" {
  bucket = "${local.name_prefix}audit-archive"
}

resource "aws_dynamodb_table" "audit_events" {
  name         = "${local.name_prefix}audit-events"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"
  range_key    = "timestamp"

  attribute {
    name = "event_id"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}

resource "aws_dynamodb_table" "file_metadata" {
  name         = "${local.name_prefix}file-metadata"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {
    name = "id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "orphan_audit" {
  name         = "${local.name_prefix}orphan-audit"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "object_key"

  attribute {
    name = "object_key"
    type = "S"
  }
}

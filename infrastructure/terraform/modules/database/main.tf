# ------------------------------------------------------------------------------
# OtterWorks Database Module
# RDS PostgreSQL and DynamoDB tables
# ------------------------------------------------------------------------------

locals {
  common_tags = {
    Module  = "database"
    Project = var.project
  }
}

# --- RDS Subnet Group ---

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-db-${var.environment}"
  subnet_ids = var.subnet_ids

  tags = merge(local.common_tags, {
    Service = "shared-database"
  })
}

# --- RDS Security Group ---

resource "aws_security_group" "rds" {
  name        = "${var.project}-rds-${var.environment}"
  description = "Security group for OtterWorks RDS PostgreSQL"
  vpc_id      = var.vpc_id

  ingress {
    description = "PostgreSQL from VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Service = "shared-database"
  })
}

# --- RDS PostgreSQL ---

resource "aws_db_instance" "postgres" {
  identifier     = "${var.project}-postgres-${var.environment}"
  engine         = "postgres"
  engine_version = "15.7"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_max_allocated_storage
  storage_encrypted     = true

  db_name  = "otterworks"
  username = "otterworks_admin"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  skip_final_snapshot = var.environment == "dev"
  deletion_protection = var.environment != "dev"

  backup_retention_period = var.environment == "dev" ? 1 : 7

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = merge(local.common_tags, {
    Service = "shared-database"
  })
}

# --- DynamoDB: File Metadata ---

resource "aws_dynamodb_table" "file_metadata" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name         = "${var.project}-file-metadata-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "id"
    type = "S"
  }

  attribute {
    name = "owner_id"
    type = "S"
  }

  attribute {
    name = "folder_id"
    type = "S"
  }

  global_secondary_index {
    name            = "owner-index"
    hash_key        = "owner_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "folder-index"
    hash_key        = "folder_id"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = var.environment != "dev"
  }

  tags = merge(local.common_tags, {
    Service = "file-service"
  })
}

# --- DynamoDB: Audit Events ---

resource "aws_dynamodb_table" "audit_events" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name         = "${var.project}-audit-events-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "id"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  global_secondary_index {
    name            = "user-index"
    hash_key        = "user_id"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  attribute {
    name = "date_partition"
    type = "S"
  }

  global_secondary_index {
    name            = "timestamp-index"
    hash_key        = "date_partition"
    range_key       = "timestamp"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.common_tags, {
    Service = "audit-service"
  })
}

# --- DynamoDB: Notifications ---

resource "aws_dynamodb_table" "notifications" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name         = "${var.project}-notifications-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "id"
    type = "S"
  }

  attribute {
    name = "userId"
    type = "S"
  }

  attribute {
    name = "createdAt"
    type = "S"
  }

  global_secondary_index {
    name            = "userId-createdAt-index"
    hash_key        = "userId"
    range_key       = "createdAt"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = var.environment != "dev"
  }

  tags = merge(local.common_tags, {
    Service = "notification-service"
  })
}

# --- DynamoDB: Folders (file-service) ---

resource "aws_dynamodb_table" "folders" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name         = "${var.project}-folders-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = var.environment != "dev"
  }

  tags = merge(local.common_tags, {
    Service = "file-service"
  })
}

# --- DynamoDB: File Versions (file-service) ---

resource "aws_dynamodb_table" "file_versions" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name         = "${var.project}-file-versions-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "file_id"
  range_key    = "version"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "file_id"
    type = "S"
  }

  attribute {
    name = "version"
    type = "N"
  }

  point_in_time_recovery {
    enabled = var.environment != "dev"
  }

  tags = merge(local.common_tags, {
    Service = "file-service"
  })
}

# --- DynamoDB: File Shares (file-service) ---

resource "aws_dynamodb_table" "file_shares" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name         = "${var.project}-file-shares-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = var.environment != "dev"
  }

  tags = merge(local.common_tags, {
    Service = "file-service"
  })
}

# --- DynamoDB: Analytics Events ---

resource "aws_dynamodb_table" "analytics_events" { # nosemgrep: terraform.aws.security.aws-dynamodb-table-unencrypted.aws-dynamodb-table-unencrypted
  name         = "${var.project}-analytics-events-${var.environment}"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  server_side_encryption {
    enabled = true
  }

  attribute {
    name = "id"
    type = "S"
  }

  attribute {
    name = "event_date"
    type = "S"
  }

  global_secondary_index {
    name            = "event-date-index"
    hash_key        = "event_date"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = merge(local.common_tags, {
    Service = "analytics-etl"
  })
}

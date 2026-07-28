# Customer-managed KMS key for the control table (SSE). Rotation on; the table
# holds durable tenant/lock/audit state so a managed key is worth the ~$1/mo.
resource "aws_kms_key" "control" {
  description             = "SSE for the ${var.control_table_name} DynamoDB control table"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  tags = {
    Name = "${var.control_table_name}-sse"
  }
}

resource "aws_kms_alias" "control" {
  name          = "alias/${var.control_table_name}-sse"
  target_key_id = aws_kms_key.control.key_id
}

# Durable control-plane state store. Independent of any ephemeral tenant:
# tenant teardown / node churn / cluster loss never affects this table.
resource "aws_dynamodb_table" "control" {
  name         = var.control_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }
  attribute {
    name = "SK"
    type = "S"
  }

  # DynamoDB TTL on lock items + informational tenant expiry.
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.control.arn
  }

  deletion_protection_enabled = true

  tags = {
    Name = var.control_table_name
  }
}

# The reaper is off unless CONFIG#reaper says otherwise, which is the right
# fail-safe but leaves a freshly installed platform with no cost control at all:
# nothing seeds this item, so TTL reaping and idle suspension silently never run
# until someone opens the dashboard. Seed it so the platform arrives in its
# documented state.
#
# The infrastructure sweep is left off. It deletes AWS resources rather than
# Kubernetes ones, so an operator should look at the orphan preview before arming
# it on an account whose contents this module cannot see.
resource "aws_dynamodb_table_item" "reaper_config" {
  table_name = aws_dynamodb_table.control.name
  hash_key   = aws_dynamodb_table.control.hash_key
  range_key  = aws_dynamodb_table.control.range_key

  item = jsonencode({
    PK                 = { S = "CONFIG#reaper" }
    SK                 = { S = "CONFIG" }
    schedule_cron      = { S = var.reaper_schedule_cron }
    grace_seconds      = { N = tostring(var.reaper_grace_seconds) }
    enabled            = { BOOL = var.reaper_enabled }
    sweep_orphans      = { BOOL = true }
    suspend_idle       = { BOOL = true }
    idle_after_seconds = { N = tostring(var.reaper_idle_after_seconds) }
    sweep_infra        = { BOOL = false }
    sweep_infra_delete = { BOOL = false }
    updated_at         = { N = "0" }
    updated_by         = { S = "terraform" }
  })

  # The dashboard owns this item once the platform is running. Terraform seeds
  # it and then stops caring, so an operator's change is not reverted by the
  # next apply.
  lifecycle {
    ignore_changes = [item]
  }
}

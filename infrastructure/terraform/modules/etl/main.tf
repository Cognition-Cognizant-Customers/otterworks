# ------------------------------------------------------------------------------
# OtterWorks ETL Module
# Serverless replacement for the legacy cron-based ETL (etl/ directory):
# EventBridge Scheduler -> Step Functions -> container-image Lambdas.
# Credentials live in Secrets Manager; alerting via SNS on state machine failure.
# ------------------------------------------------------------------------------

locals {
  common_tags = {
    Module  = "etl"
    Project = var.project
  }

  name_prefix = "${var.project}-etl"

  # Retry policy applied to every Lambda task state (exponential backoff).
  common_retry = jsonencode({
    ErrorEquals = [
      "Lambda.ServiceException",
      "Lambda.TooManyRequestsException",
      "Lambda.SdkClientException",
      "States.TaskFailed",
    ]
    IntervalSeconds = 30
    MaxAttempts     = 3
    BackoffRate     = 2.0
  })

  common_env = {
    DATA_LAKE_BUCKET       = var.data_lake_bucket_name
    FILE_STORAGE_BUCKET    = var.file_bucket_name
    QUARANTINE_BUCKET      = aws_s3_bucket.quarantine.id
    ARCHIVE_BUCKET         = var.audit_archive_bucket_name
    ANALYTICS_PREFIX       = var.analytics_prefix
    ANALYTICS_QUEUE_URL    = var.analytics_queue_url
    ANALYTICS_DLQ_URL      = aws_sqs_queue.analytics_dlq.url
    ANALYTICS_EVENTS_TABLE = var.analytics_events_table_name
    AUDIT_EVENTS_TABLE     = var.audit_events_table_name
    FILE_METADATA_TABLE    = var.file_metadata_table_name
    DB_SECRET_ID           = aws_secretsmanager_secret.etl_db.name
    MEILISEARCH_SECRET_ID  = aws_secretsmanager_secret.meilisearch.name
    DOCUMENT_SERVICE_URL   = var.document_service_url
    FILE_SERVICE_URL       = var.file_service_url
    MEILISEARCH_URL        = var.meilisearch_url
  }

  # schedule expressions mirror the legacy crontab (UTC).
  # in_vpc: only pipelines that reach in-VPC resources (RDS, MeiliSearch,
  # internal service APIs) are VPC-attached; the rest use AWS APIs only and
  # keep the Lambda service's default network path.
  pipelines = {
    analytics = {
      handler  = "otterworks_etl.analytics.handler.handler"
      schedule = "cron(0 2 * * ? *)"
      asl_file = "analytics.asl.json"
      in_vpc   = true
    }
    audit-archive = {
      handler  = "otterworks_etl.audit_archive.handler.handler"
      schedule = "cron(0 3 ? * SUN *)"
      asl_file = "audit_archive.asl.json"
      in_vpc   = false
    }
    search-reindex = {
      handler  = "otterworks_etl.search_reindex.handler.handler"
      schedule = "cron(0 4 ? * SUN *)"
      asl_file = "search_reindex.asl.json"
      in_vpc   = true
    }
    storage-cleanup = {
      handler  = "otterworks_etl.storage_cleanup.handler.handler"
      schedule = "cron(30 2 * * ? *)"
      asl_file = "storage_cleanup.asl.json"
      in_vpc   = false
    }
    user-activity = {
      handler  = "otterworks_etl.user_activity.handler.handler"
      schedule = "cron(0 5 * * ? *)"
      asl_file = "user_activity.asl.json"
      in_vpc   = true
    }
  }
}

# --- Quarantine bucket (was referenced by legacy config, never provisioned) ---

resource "aws_s3_bucket" "quarantine" {
  bucket = "${var.project}-file-quarantine-${var.environment}"

  tags = merge(local.common_tags, {
    Service = "etl-storage-cleanup"
  })
}

resource "aws_s3_bucket_public_access_block" "quarantine" {
  bucket                  = aws_s3_bucket.quarantine.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_policy" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.quarantine.arn,
          "${aws_s3_bucket.quarantine.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.quarantine]
}

resource "aws_s3_bucket_lifecycle_configuration" "quarantine" {
  bucket = aws_s3_bucket.quarantine.id

  rule {
    id     = "expire-quarantined-objects"
    status = "Enabled"

    filter {
      prefix = "quarantined/"
    }

    expiration {
      days = 90
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "remove-expired-delete-markers"
    status = "Enabled"

    filter {
      prefix = ""
    }

    expiration {
      expired_object_delete_marker = true
    }
  }
}

# Intermediate hand-off datasets written by the Lambda tasks; without an
# expiry they accumulate for every run of every pipeline forever. Kept for
# 14 days so analytics re-runs can still reuse staged SQS events.
resource "aws_s3_bucket_lifecycle_configuration" "etl_staging" {
  bucket = var.data_lake_bucket_name

  rule {
    id     = "expire-etl-staging"
    status = "Enabled"

    filter {
      prefix = "etl-staging/"
    }

    expiration {
      days = 14
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# --- DLQ for malformed analytics events ---

resource "aws_sqs_queue" "analytics_dlq" {
  name                      = "${local.name_prefix}-analytics-dlq-${var.environment}"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true

  tags = local.common_tags
}

# --- Secrets (values are set out-of-band, never in Terraform state) ---

resource "aws_secretsmanager_secret" "etl_db" {
  name        = "${local.name_prefix}/${var.environment}/database"
  description = "PostgreSQL credentials for the ETL pipelines (host, port, database, username, password)"

  tags = local.common_tags
}

resource "aws_secretsmanager_secret" "meilisearch" {
  name        = "${local.name_prefix}/${var.environment}/meilisearch"
  description = "MeiliSearch API key for the search reindex pipeline (api_key)"

  tags = local.common_tags
}

# --- Alerting ---

resource "aws_sns_topic" "etl_alerts" {
  name = "${local.name_prefix}-alerts-${var.environment}"

  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "etl_alerts_email" {
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.etl_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# --- Lambda networking ---

resource "aws_security_group" "etl_lambda" {
  name        = "${local.name_prefix}-lambda-${var.environment}"
  description = "ETL Lambda functions (egress to VPC services and AWS APIs)"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

# VPC endpoints so VPC-attached pipelines can reach AWS APIs without a NAT
# gateway (the platform's private subnets have no NAT route by default).

data "aws_region" "current" {}

data "aws_route_table" "lambda_subnets" {
  for_each = toset(var.subnet_ids)

  subnet_id = each.value
}

resource "aws_vpc_endpoint" "gateway" {
  for_each = toset(["s3", "dynamodb"])

  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.${each.value}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = distinct([for rt in data.aws_route_table.lambda_subnets : rt.id])

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-${each.value}-${var.environment}"
  })
}

resource "aws_security_group" "vpc_endpoints" {
  name        = "${local.name_prefix}-vpce-${var.environment}"
  description = "HTTPS from ETL Lambdas to AWS service interface endpoints"
  vpc_id      = var.vpc_id

  # private DNS makes these endpoints authoritative for the whole VPC, so
  # every in-VPC consumer of SQS/Secrets Manager must be able to reach them
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = local.common_tags
}

resource "aws_vpc_endpoint" "interface" {
  for_each = toset(["sqs", "secretsmanager"])

  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${data.aws_region.current.name}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = var.subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-${each.value}-${var.environment}"
  })
}

# --- Lambda execution roles (one per pipeline, least-privilege) ---

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  for_each = local.pipelines

  name               = "${local.name_prefix}-${each.key}-lambda-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  for_each = local.pipelines

  role       = aws_iam_role.lambda[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

locals {
  staging_objects_arn = "${var.data_lake_bucket_arn}/etl-staging/*"

  pipeline_policies = {
    analytics = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:DeleteMessageBatch", "sqs:GetQueueAttributes"]
        Resource = [var.analytics_queue_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = [aws_sqs_queue.analytics_dlq.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:Scan"]
        Resource = [var.analytics_events_table_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = ["${var.data_lake_bucket_arn}/*"]
      },
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = [var.data_lake_bucket_arn]
        Condition = { StringLike = { "s3:prefix" = ["etl-staging/*"] } }
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.etl_db.arn]
      },
    ]
    audit-archive = [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:DescribeTable", "dynamodb:Scan", "dynamodb:BatchWriteItem", "dynamodb:DeleteItem"]
        Resource = [var.audit_events_table_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${var.audit_archive_bucket_arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = [local.staging_objects_arn]
      },
    ]
    search-reindex = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.meilisearch.arn]
      },
    ]
    storage-cleanup = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [var.file_bucket_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:DeleteObject"]
        Resource = ["${var.file_bucket_arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${aws_s3_bucket.quarantine.arn}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:Scan"]
        Resource = [var.file_metadata_table_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = ["${var.data_lake_bucket_arn}/*"]
      },
    ]
    user-activity = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = ["${var.data_lake_bucket_arn}/*"]
      },
      {
        # lets GetObject on a missing daily key return NoSuchKey (404)
        # instead of AccessDenied
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [var.data_lake_bucket_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.etl_db.arn]
      },
    ]
  }
}

resource "aws_iam_role_policy" "lambda" {
  for_each = local.pipelines

  name = "${local.name_prefix}-${each.key}-${var.environment}"
  role = aws_iam_role.lambda[each.key].id

  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = local.pipeline_policies[each.key]
  })
}

# --- Lambda functions (single container image, per-pipeline handler) ---

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.pipelines

  name              = "/aws/lambda/${local.name_prefix}-${each.key}-${var.environment}"
  retention_in_days = var.log_retention_days

  tags = local.common_tags
}

resource "aws_lambda_function" "pipeline" {
  for_each = local.pipelines

  function_name = "${local.name_prefix}-${each.key}-${var.environment}"
  role          = aws_iam_role.lambda[each.key].arn
  package_type  = "Image"
  image_uri     = var.image_uri
  timeout       = 900
  memory_size   = 1024

  image_config {
    command = [each.value.handler]
  }

  environment {
    variables = local.common_env
  }

  dynamic "vpc_config" {
    for_each = each.value.in_vpc ? [1] : []
    content {
      subnet_ids         = var.subnet_ids
      security_group_ids = [aws_security_group.etl_lambda.id]
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]

  tags = local.common_tags
}

# --- Step Functions state machines ---

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${local.name_prefix}-sfn-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json

  tags = local.common_tags
}

resource "aws_iam_role_policy" "sfn" {
  name = "${local.name_prefix}-sfn-${var.environment}"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = [for fn in aws_lambda_function.pipeline : fn.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = [aws_sns_topic.etl_alerts.arn]
      },
    ]
  })
}

resource "aws_sfn_state_machine" "pipeline" {
  for_each = local.pipelines

  name     = "${local.name_prefix}-${each.key}-${var.environment}"
  role_arn = aws_iam_role.sfn.arn

  definition = templatefile("${path.module}/statemachines/${each.value.asl_file}", {
    lambda_arn      = aws_lambda_function.pipeline[each.key].arn
    alert_topic_arn = aws_sns_topic.etl_alerts.arn
    common_retry    = local.common_retry
  })

  tags = local.common_tags
}

# --- EventBridge Scheduler (replaces crontab) ---

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.name_prefix}-scheduler-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json

  tags = local.common_tags
}

resource "aws_iam_role_policy" "scheduler" {
  name = "${local.name_prefix}-scheduler-${var.environment}"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = [for sm in aws_sfn_state_machine.pipeline : sm.arn]
      },
    ]
  })
}

resource "aws_scheduler_schedule" "pipeline" {
  for_each = local.pipelines

  name                         = "${local.name_prefix}-${each.key}-${var.environment}"
  schedule_expression          = each.value.schedule
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.pipeline[each.key].arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ ds = "<aws.scheduler.scheduled-time>" })
  }
}

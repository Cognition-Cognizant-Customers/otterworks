data "archive_file" "orphan_quarantine" {
  type        = "zip"
  source_dir  = "${path.root}/../../lambda/ow-tp-orphan-quarantine"
  output_path = "${path.root}/.terraform/ow-tp-orphan-quarantine.zip"
}

resource "aws_cloudwatch_log_group" "orphan_quarantine" {
  name              = "/aws/lambda/ow-tp-orphan-quarantine"
  retention_in_days = 14
}

resource "aws_sqs_queue" "orphan_quarantine_dlq" {
  name = "ow-tp-orphan-quarantine-dlq"
}

resource "aws_iam_role" "orphan_quarantine" {
  name = "ow-tp-orphan-quarantine-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "orphan_quarantine" {
  name = "ow-tp-orphan-quarantine-policy"
  role = aws_iam_role.orphan_quarantine.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.file_storage.arn}/files/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.file_quarantine.arn}/*"
      },
      {
        Effect = "Allow"
        Action = "s3:ListBucket"
        Resource = [
          aws_s3_bucket.file_storage.arn,
          aws_s3_bucket.file_quarantine.arn,
        ]
        Condition = {
          StringLike = {
            "s3:prefix" = ["files/*", "quarantined/*"]
          }
        }
      },
      {
        Effect   = "Allow"
        Action   = "dynamodb:Scan"
        Resource = aws_dynamodb_table.file_metadata.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.orphan_audit.arn
      },
      {
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.orphan_quarantine_dlq.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.orphan_quarantine.arn}:*"
      },
    ]
  })
}

resource "aws_lambda_function" "orphan_quarantine" {
  function_name    = "ow-tp-orphan-quarantine"
  role             = aws_iam_role.orphan_quarantine.arn
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.orphan_quarantine.output_path
  source_code_hash = data.archive_file.orphan_quarantine.output_base64sha256
  timeout          = 120
  memory_size      = 256

  dead_letter_config {
    target_arn = aws_sqs_queue.orphan_quarantine_dlq.arn
  }

  environment {
    variables = {
      STORAGE_BUCKET        = aws_s3_bucket.file_storage.bucket
      QUARANTINE_BUCKET     = aws_s3_bucket.file_quarantine.bucket
      METADATA_TABLE        = aws_dynamodb_table.file_metadata.name
      AUDIT_TABLE           = aws_dynamodb_table.orphan_audit.name
      FILES_PREFIX          = "files/"
      QUARANTINE_PREFIX     = "quarantined"
      RECHECK_DELAY_SECONDS = "45"
    }
  }

  depends_on = [aws_cloudwatch_log_group.orphan_quarantine]
}

resource "aws_sqs_queue_policy" "orphan_quarantine_dlq" {
  queue_url = aws_sqs_queue.orphan_quarantine_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "events.amazonaws.com"
      }
      Action   = "sqs:SendMessage"
      Resource = aws_sqs_queue.orphan_quarantine_dlq.arn
      Condition = {
        ArnEquals = {
          "aws:SourceArn" = aws_cloudwatch_event_rule.orphan_detect.arn
        }
      }
    }]
  })
}

resource "aws_s3_bucket_notification" "file_storage_eventbridge" {
  bucket      = aws_s3_bucket.file_storage.id
  eventbridge = true
}

resource "aws_cloudwatch_event_rule" "orphan_detect" {
  name = "ow-tp-orphan-detect"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [aws_s3_bucket.file_storage.bucket]
      }
      object = {
        key = [{
          prefix = "files/"
        }]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "orphan_quarantine" {
  rule      = aws_cloudwatch_event_rule.orphan_detect.name
  target_id = "ow-tp-orphan-quarantine"
  arn       = aws_lambda_function.orphan_quarantine.arn

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 3
  }

  dead_letter_config {
    arn = aws_sqs_queue.orphan_quarantine_dlq.arn
  }
}

resource "aws_lambda_permission" "orphan_detect" {
  statement_id  = "AllowEventBridgeOrphanDetect"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orphan_quarantine.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.orphan_detect.arn
}

resource "aws_s3_bucket_lifecycle_configuration" "file_quarantine" {
  bucket = aws_s3_bucket.file_quarantine.id

  rule {
    id     = "expire-quarantined-objects"
    status = "Enabled"

    filter {
      prefix = "quarantined/"
    }

    expiration {
      days = 30
    }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

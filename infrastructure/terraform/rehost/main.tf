# ------------------------------------------------------------------------------
# OtterWorks Rehost (lift-and-shift) - legacy-portal on EC2 + RDS
#
# Standalone Terraform root for the rehost demo: lifts the legacy-portal
# modular monolith from its on-prem/VM deployment (fat JAR under systemd,
# local PostgreSQL) onto AWS as-is:
#   - one EC2 instance running the same fat JAR under the same systemd unit
#   - RDS PostgreSQL replacing the co-located on-prem PostgreSQL
#   - an S3 artifact bucket the deploy script uploads the JAR to
#
# Deliberately separate from the EKS/Helm path (this is a rehost, not a
# re-platform). Reuses the platform VPC via remote state.
# ------------------------------------------------------------------------------

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }

  backend "s3" {
    bucket = "otterworks-terraform-state"
    key    = "rehost/legacy-portal/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "otterworks"
      Environment = var.environment
      ManagedBy   = "terraform"
      Layer       = "rehost"
      Service     = "legacy-portal"
    }
  }
}

# --- Platform VPC (from /platform/terraform) ---

data "terraform_remote_state" "platform" {
  backend = "s3"

  config = {
    bucket = "otterworks-terraform-state"
    key    = "platform/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  vpc_id          = data.terraform_remote_state.platform.outputs.vpc_id
  vpc_cidr        = data.terraform_remote_state.platform.outputs.vpc_cidr_block
  public_subnets  = data.terraform_remote_state.platform.outputs.public_subnet_ids
  private_subnets = data.terraform_remote_state.platform.outputs.private_subnet_ids

  name = "otterworks-legacy-portal-${var.environment}"
}

# --- Artifact bucket (deploy script uploads the fat JAR here) ---

resource "aws_s3_bucket" "artifacts" {
  bucket = "${local.name}-artifacts"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

data "aws_iam_policy_document" "artifacts_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = data.aws_iam_policy_document.artifacts_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}

# --- Security groups ---

resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "legacy-portal EC2 instance"
  vpc_id      = local.vpc_id

  # Ingress is opt-in: no rule is created unless CIDRs are explicitly provided
  # (the app serves unauthenticated plain-HTTP endpoints on 8095).
  dynamic "ingress" {
    for_each = length(var.app_ingress_cidr_blocks) > 0 ? [1] : []

    content {
      description = "legacy-portal HTTP"
      from_port   = 8095
      to_port     = 8095
      protocol    = "tcp"
      cidr_blocks = var.app_ingress_cidr_blocks
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "legacy-portal RDS PostgreSQL"
  vpc_id      = local.vpc_id

  ingress {
    description     = "PostgreSQL from legacy-portal instance"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- RDS PostgreSQL (replaces the co-located on-prem PostgreSQL) ---

resource "aws_db_subnet_group" "legacy_portal" {
  name       = local.name
  subnet_ids = local.private_subnets
}

resource "aws_db_instance" "legacy_portal" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = "15.7"
  instance_class = var.db_instance_class

  allocated_storage = var.db_allocated_storage
  storage_encrypted = true

  db_name  = "legacyportal"
  username = "legacyportal"

  # RDS generates and owns the master password in Secrets Manager; it never
  # appears in Terraform variables, state, or EC2 user-data.
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.legacy_portal.name
  vpc_security_group_ids = [aws_security_group.db.id]

  skip_final_snapshot = var.environment == "dev"
  deletion_protection = var.environment != "dev"

  backup_retention_period = var.environment == "dev" ? 1 : 7

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = {
    Name = local.name
  }
}

# --- IAM: instance reads the JAR from the artifact bucket; SSM for ops ---

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "app" {
  name               = local.name
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
}

data "aws_iam_policy_document" "app" {
  statement {
    actions = ["s3:GetObject", "s3:ListBucket"]
    resources = [
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
    ]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.legacy_portal.master_user_secret[0].secret_arn]
  }
}

resource "aws_iam_role_policy" "app" {
  name   = "artifact-read"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.app.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "app" {
  name = local.name
  role = aws_iam_role.app.name
}

# --- EC2 instance (the lifted VM) ---

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# The rehosted VM is intentionally internet-facing on 8095 only (public subnet,
# no SSH; ops via SSM), mirroring the exposed on-prem host it lifts.
resource "aws_instance" "app" { # nosemgrep: terraform.aws.security.aws-ec2-has-public-ip.aws-ec2-has-public-ip
  ami           = coalesce(var.ami_id, data.aws_ami.al2023.id)
  instance_type = var.instance_type

  subnet_id                   = local.public_subnets[0]
  vpc_security_group_ids      = [aws_security_group.app.id]
  associate_public_ip_address = true
  iam_instance_profile        = aws_iam_instance_profile.app.name

  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    artifact_bucket = aws_s3_bucket.artifacts.bucket
    artifact_key    = var.artifact_key
    db_endpoint     = aws_db_instance.legacy_portal.endpoint
    db_name         = aws_db_instance.legacy_portal.db_name
    db_username     = aws_db_instance.legacy_portal.username
    db_secret_arn   = aws_db_instance.legacy_portal.master_user_secret[0].secret_arn
  })
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_size = 16
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name = local.name
  }

  # The AMI lookup uses most_recent, which re-resolves on every plan; ignore
  # drift so a monthly AL2023 release can't replace the instance during an
  # unrelated apply. Pin var.ami_id (or taint) to move to a newer image.
  lifecycle {
    ignore_changes = [ami]
  }

  # User-data reads the DB secret and the artifact bucket on first boot, so the
  # role's permissions must exist before the instance launches.
  depends_on = [
    aws_iam_role_policy.app,
    aws_iam_role_policy_attachment.ssm,
  ]
}

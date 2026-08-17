# ------------------------------------------------------------------------------
# OtterWorks Search Module
# MeiliSearch instance for full-text search
# ------------------------------------------------------------------------------

locals {
  common_tags = {
    Module  = "search"
    Project = var.project
  }
}

# --- MeiliSearch Security Group ---

resource "aws_security_group" "meilisearch" {
  name        = "${var.project}-meilisearch-${var.environment}"
  description = "Security group for OtterWorks MeiliSearch"
  vpc_id      = var.vpc_id

  ingress {
    description = "MeiliSearch HTTP from VPC"
    from_port   = 7700
    to_port     = 7700
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
    Service = "search-service"
  })
}

# --- ECS Execution Role ---

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name               = "${var.project}-meilisearch-ecs-exec-${var.environment}"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_secrets_access" {
  count = var.meilisearch_master_key != "" ? 1 : 0
  name  = "${var.project}-meilisearch-secrets-${var.environment}"
  role  = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [aws_secretsmanager_secret.meilisearch_master_key[0].arn]
      }
    ]
  })
}

# --- Secrets Manager ---

resource "aws_secretsmanager_secret" "meilisearch_master_key" {
  count       = var.meilisearch_master_key != "" ? 1 : 0
  name        = "${var.project}/${var.environment}/meilisearch-master-key"
  description = "MeiliSearch master key for ${var.environment}"

  tags = merge(local.common_tags, {
    Service = "search-service"
  })
}

resource "aws_secretsmanager_secret_version" "meilisearch_master_key" {
  count         = var.meilisearch_master_key != "" ? 1 : 0
  secret_id     = aws_secretsmanager_secret.meilisearch_master_key[0].id
  secret_string = var.meilisearch_master_key
}

# --- ECS Cluster ---

resource "aws_ecs_cluster" "meilisearch" {
  name = "${var.project}-meilisearch-${var.environment}"

  tags = merge(local.common_tags, {
    Service = "search-service"
  })
}

# --- CloudWatch Log Group ---

resource "aws_cloudwatch_log_group" "meilisearch" {
  name              = "/ecs/${var.project}-meilisearch-${var.environment}"
  retention_in_days = 30

  tags = local.common_tags
}

# --- ECS Task Definition ---

resource "aws_ecs_task_definition" "meilisearch" {
  family                   = "${var.project}-meilisearch-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.meilisearch_cpu
  memory                   = var.meilisearch_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([
    {
      name      = "meilisearch"
      image     = "getmeili/meilisearch:v1.6"
      essential = true
      portMappings = [
        {
          containerPort = 7700
          hostPort      = 7700
          protocol      = "tcp"
        }
      ]
      environment = [
        { name = "MEILI_ENV", value = var.environment == "prod" ? "production" : "development" },
        { name = "MEILI_NO_ANALYTICS", value = "true" },
      ]
      secrets = var.meilisearch_master_key != "" ? [
        {
          name      = "MEILI_MASTER_KEY"
          valueFrom = aws_secretsmanager_secret.meilisearch_master_key[0].arn
        },
      ] : []
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.meilisearch.name
          "awslogs-region"        = data.aws_region.current.region
          "awslogs-stream-prefix" = "meilisearch"
        }
      }
    }
  ])

  lifecycle {
    precondition {
      condition     = var.environment != "prod" || var.meilisearch_master_key != ""
      error_message = "meilisearch_master_key is required when environment is 'prod'. MeiliSearch refuses to start in production mode without MEILI_MASTER_KEY."
    }
  }

  tags = merge(local.common_tags, {
    Service = "search-service"
  })
}

data "aws_region" "current" {}

# --- ECS Service ---

resource "aws_ecs_service" "meilisearch" {
  name            = "${var.project}-meilisearch-${var.environment}"
  cluster         = aws_ecs_cluster.meilisearch.id
  task_definition = aws_ecs_task_definition.meilisearch.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = [aws_security_group.meilisearch.id]
    assign_public_ip = false
  }

  tags = merge(local.common_tags, {
    Service = "search-service"
  })
}

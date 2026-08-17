provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "otterworks-tp"
      Namespace = var.namespace
      Estate    = "legacy-portal-decomposition"
    }
  }
}

locals {
  prefix = "${var.name_prefix}-${var.namespace}"

  # One entry per bounded context of the legacy portal. Each context gets its own
  # Lambda, DynamoDB table, IAM role, and API routes — the monolith's module seams
  # become service boundaries.
  services = {
    announcements = {
      handler  = "com.otterworks.portal.announcements.Handler::handleRequest"
      jar      = "${path.module}/../announcements-service/target/announcements-service.jar"
      hash_key = "pk"
      key_type = "N"
      routes = [
        "GET /health",
        "GET /api/announcements",
        "POST /api/announcements",
        "GET /api/announcements/{id}",
        "POST /api/announcements/{id}/publish",
      ]
    }
    preferences = {
      handler  = "com.otterworks.portal.preferences.Handler::handleRequest"
      jar      = "${path.module}/../preferences-service/target/preferences-service.jar"
      hash_key = "userId"
      key_type = "S"
      routes = [
        "GET /api/preferences/{userId}",
        "PUT /api/preferences/{userId}",
      ]
    }
    feedback = {
      handler  = "com.otterworks.portal.feedback.Handler::handleRequest"
      jar      = "${path.module}/../feedback-service/target/feedback-service.jar"
      hash_key = "pk"
      key_type = "N"
      routes = [
        "POST /api/feedback",
        "GET /api/feedback",
        "GET /api/feedback/average-rating",
      ]
    }
  }

  service_routes = merge([
    for name, svc in local.services : {
      for route in svc.routes : "${name}|${route}" => { service = name, route_key = route }
    }
  ]...)
}

resource "aws_apigatewayv2_api" "portal" {
  name          = "${local.prefix}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "PUT", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.portal.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_burst_limit = 50
    throttling_rate_limit  = 100
  }
}

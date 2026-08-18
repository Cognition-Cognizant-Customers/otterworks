# Edge for the hosted demo page: CloudFront in front of the S3 website origin,
# with a WAFv2 web ACL (AWS managed common rule set + a rate-based rule). Both
# are pay-per-request edge components — no ALB, nothing with hourly idle cost.
#
# The WAF's CLOUDFRONT scope requires us-east-1, which is where this estate
# already lives; no provider alias is needed.
#
# The rate-based rule is the "burst shed" demo beat: a client exceeding
# waf_rate_limit requests per 5 minutes starts receiving 403s from the edge.
# The load test targets the API Gateway URL directly, so the WAF never sheds
# the traffic being measured.

resource "aws_wafv2_web_acl" "demo_site" {
  count = var.enable_demo_site ? 1 : 0

  name        = "${local.prefix}-demo-site-waf"
  description = "Managed common rules + rate limit for the portal demo page CDN."
  scope       = "CLOUDFRONT"

  default_action {
    allow {}
  }

  rule {
    name     = "common-rule-set"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.prefix}-common-rules"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "rate-limit"
    priority = 2

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.waf_rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.prefix}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.prefix}-demo-site-waf"
    sampled_requests_enabled   = true
  }
}

resource "aws_cloudfront_distribution" "demo_site" {
  count = var.enable_demo_site ? 1 : 0

  enabled             = true
  comment             = "${local.prefix} demo page CDN"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  web_acl_id          = aws_wafv2_web_acl.demo_site[0].arn

  origin {
    origin_id   = "demo-site-s3-website"
    domain_name = aws_s3_bucket_website_configuration.demo_site[0].website_endpoint

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    target_origin_id       = "demo-site-s3-website"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 60
    max_ttl     = 300
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

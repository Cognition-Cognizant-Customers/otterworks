# Optional S3 static website hosting the Otter Portal demo page, so the "after"
# UI lives in AWS next to the estate. The page itself also runs locally via
# scripts/tp_portal/demo_server.py for the before-state act.

resource "aws_s3_bucket" "demo_site" {
  count = var.enable_demo_site ? 1 : 0

  bucket        = "${local.prefix}-demo-site"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "demo_site" {
  count = var.enable_demo_site ? 1 : 0

  bucket                  = aws_s3_bucket.demo_site[0].id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "demo_site" {
  count = var.enable_demo_site ? 1 : 0

  bucket = aws_s3_bucket.demo_site[0].id

  index_document {
    suffix = "index.html"
  }
}

resource "aws_s3_bucket_policy" "demo_site" {
  count = var.enable_demo_site ? 1 : 0

  bucket = aws_s3_bucket.demo_site[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicRead"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.demo_site[0].arn}/*"
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.demo_site]
}

resource "aws_s3_object" "demo_page" {
  count = var.enable_demo_site ? 1 : 0

  bucket       = aws_s3_bucket.demo_site[0].id
  key          = "index.html"
  source       = "${path.module}/../demo-ui/index.html"
  etag         = filemd5("${path.module}/../demo-ui/index.html")
  content_type = "text/html"
}

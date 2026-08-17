output "api_base_url" {
  description = "Public HTTPS base URL of the decomposed portal (paste into the Otter Portal demo page)."
  value       = aws_apigatewayv2_api.portal.api_endpoint
}

output "demo_site_url" {
  description = "S3-hosted Otter Portal demo page (empty when enable_demo_site=false)."
  value = var.enable_demo_site ? "http://${aws_s3_bucket_website_configuration.demo_site[0].website_endpoint}" : ""
}

output "lambda_functions" {
  description = "Function name per bounded context."
  value       = { for name, fn in aws_lambda_function.service : name => fn.function_name }
}

output "dynamodb_tables" {
  description = "Table name per bounded context."
  value       = { for name, t in aws_dynamodb_table.context : name => t.name }
}

output "error_alarms" {
  description = "CloudWatch alarm name per bounded context."
  value       = { for name, a in aws_cloudwatch_metric_alarm.lambda_errors : name => a.alarm_name }
}

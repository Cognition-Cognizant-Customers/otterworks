output "control_table_name" {
  value = aws_dynamodb_table.control.name
}

output "control_table_arn" {
  value = aws_dynamodb_table.control.arn
}

output "dashboard_role_arn" {
  value = aws_iam_role.dashboard.arn
}

output "dns_zone_id" {
  value = var.enable_dns ? data.aws_route53_zone.demo[0].zone_id : null
}

output "dns_zone_name_servers" {
  value = var.enable_dns ? data.aws_route53_zone.demo[0].name_servers : null
}

output "dns_role_arn" {
  value = var.enable_dns ? aws_iam_role.dns[0].arn : null
}

output "provisioner_user_arn" {
  value = aws_iam_user.provisioner.arn
}

output "dashboard_passcode_secret_arn" {
  value = aws_secretsmanager_secret.dashboard_passcode.arn
}

# Set this as the AWS_ROLE_ARN repository secret (see .github/workflows/cd-tenant.yml).
output "github_actions_role_arn" {
  value = var.enable_github_actions_role ? aws_iam_role.github_actions[0].arn : null
}

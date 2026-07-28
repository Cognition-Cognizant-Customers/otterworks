output "cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "EKS cluster API endpoint"
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_certificate_authority" {
  description = "Base64-encoded cluster CA certificate"
  value       = aws_eks_cluster.main.certificate_authority[0].data
}

output "cluster_version" {
  description = "Kubernetes version"
  value       = aws_eks_cluster.main.version
}

output "cluster_security_group_id" {
  description = "Cluster security group ID"
  value       = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
}

output "oidc_provider_arn" {
  description = "ARN of the OIDC provider for IRSA"
  value       = aws_iam_openid_connect_provider.eks.arn
}

output "oidc_provider_url" {
  description = "URL of the OIDC provider"
  value       = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

output "ebs_csi_driver_role_arn" {
  description = "IAM role ARN for the EBS CSI driver"
  value       = aws_iam_role.ebs_csi_driver.arn
}

output "node_group_role_arn" {
  description = "IAM role ARN for the node group"
  value       = aws_iam_role.node_group.arn
}

output "karpenter_controller_role_arn" {
  description = "IAM role ARN the Karpenter controller assumes via IRSA"
  value       = try(aws_iam_role.karpenter_controller[0].arn, null)
}

output "karpenter_node_instance_profile" {
  description = "Instance profile Karpenter launches nodes with"
  value       = try(aws_iam_instance_profile.karpenter_node[0].name, null)
}

output "karpenter_interruption_queue" {
  description = "SQS queue carrying Spot interruption and rebalance notices"
  value       = try(aws_sqs_queue.karpenter[0].name, null)
}

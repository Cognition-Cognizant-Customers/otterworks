# --- VPC ---

output "vpc_id" {
  description = "VPC ID"
  value       = module.vpc.vpc_id
}

output "vpc_cidr_block" {
  description = "VPC CIDR block"
  value       = module.vpc.vpc_cidr_block
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = module.vpc.public_subnet_ids
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = module.vpc.private_subnet_ids
}

# --- EKS ---

output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS cluster API endpoint"
  value       = module.eks.cluster_endpoint
}

output "cluster_certificate_authority" {
  description = "Base64-encoded cluster CA certificate"
  value       = module.eks.cluster_certificate_authority
}

output "cluster_version" {
  description = "Kubernetes version"
  value       = module.eks.cluster_version
}

output "oidc_provider_arn" {
  description = "ARN of the OIDC provider for IRSA"
  value       = module.eks.oidc_provider_arn
}

output "oidc_provider_url" {
  description = "URL of the OIDC provider"
  value       = module.eks.oidc_provider_url
}

output "ebs_csi_driver_role_arn" {
  description = "IAM role ARN for the EBS CSI driver"
  value       = module.eks.ebs_csi_driver_role_arn
}

# --- ECR ---

output "ecr_repository_urls" {
  description = "Map of service name to ECR repository URL"
  value       = module.ecr.repository_urls
}

output "karpenter_controller_role_arn" {
  description = "IAM role ARN the Karpenter controller assumes via IRSA"
  value       = module.eks.karpenter_controller_role_arn
}

output "karpenter_node_instance_profile" {
  description = "Instance profile Karpenter launches nodes with"
  value       = module.eks.karpenter_node_instance_profile
}

output "karpenter_interruption_queue" {
  description = "SQS queue carrying Spot interruption and rebalance notices"
  value       = module.eks.karpenter_interruption_queue
}

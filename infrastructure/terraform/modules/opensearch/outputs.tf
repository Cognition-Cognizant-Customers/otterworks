output "collection_arn" {
  description = "ARN of the OpenSearch Serverless collection"
  value       = aws_opensearchserverless_collection.search.arn
}

output "collection_id" {
  description = "ID of the OpenSearch Serverless collection"
  value       = aws_opensearchserverless_collection.search.id
}

output "collection_name" {
  description = "Name of the OpenSearch Serverless collection"
  value       = aws_opensearchserverless_collection.search.name
}

output "collection_endpoint" {
  description = "HTTPS endpoint of the OpenSearch Serverless collection"
  value       = aws_opensearchserverless_collection.search.collection_endpoint
}

output "dashboard_endpoint" {
  description = "OpenSearch Dashboards endpoint for the collection"
  value       = aws_opensearchserverless_collection.search.dashboard_endpoint
}

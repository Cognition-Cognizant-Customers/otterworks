output "file_storage_bucket" {
  value = aws_s3_bucket.file_storage.bucket
}

output "file_quarantine_bucket" {
  value = aws_s3_bucket.file_quarantine.bucket
}

output "audit_archive_bucket" {
  value = aws_s3_bucket.audit_archive.bucket
}

output "audit_events_table" {
  value = aws_dynamodb_table.audit_events.name
}

output "file_metadata_table" {
  value = aws_dynamodb_table.file_metadata.name
}

output "orphan_audit_table" {
  value = aws_dynamodb_table.orphan_audit.name
}

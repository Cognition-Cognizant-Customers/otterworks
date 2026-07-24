use aws_sdk_s3::presigning::PresigningConfig;
use bytes::Bytes;
use std::time::Duration;

use crate::config::AwsConfig;
use crate::errors::ServiceError;

/// S3 client for file blob operations.
#[derive(Clone)]
pub struct S3Client {
    pub client: aws_sdk_s3::Client,
    pub bucket: String,
}

impl S3Client {
    pub async fn new(config: &AwsConfig) -> Self {
        let mut aws_config_builder = aws_config::defaults(aws_config::BehaviorVersion::latest())
            .region(aws_config::Region::new(config.region.clone()));

        if let Some(endpoint) = &config.endpoint_url {
            aws_config_builder = aws_config_builder.endpoint_url(endpoint);
        }

        let aws_config = aws_config_builder.load().await;
        let s3_config = aws_sdk_s3::config::Builder::from(&aws_config)
            .force_path_style(true)
            .build();
        let client = aws_sdk_s3::Client::from_conf(s3_config);

        Self {
            client,
            bucket: config.s3_bucket.clone(),
        }
    }

    /// Upload file content to S3.
    pub async fn upload_object(
        &self,
        key: &str,
        body: Bytes,
        content_type: &str,
    ) -> Result<(), ServiceError> {
        self.client
            .put_object()
            .bucket(&self.bucket)
            .key(key)
            .body(body.into())
            .content_type(content_type)
            .send()
            .await
            .map_err(|e| ServiceError::S3Error(format!("upload failed: {e}")))?;

        tracing::info!(key = %key, bucket = %self.bucket, "Uploaded object to S3");
        Ok(())
    }

    /// Download file content from S3.
    pub async fn download_object(&self, key: &str) -> Result<Bytes, ServiceError> {
        let resp = self
            .client
            .get_object()
            .bucket(&self.bucket)
            .key(key)
            .send()
            .await
            .map_err(|e| ServiceError::S3Error(format!("download failed: {e}")))?;

        let body = resp
            .body
            .collect()
            .await
            .map_err(|e| ServiceError::S3Error(format!("body read failed: {e}")))?;

        Ok(body.into_bytes())
    }

    /// Generate a presigned download URL.
    pub async fn presigned_download_url(
        &self,
        key: &str,
        expires_in_secs: u64,
    ) -> Result<String, ServiceError> {
        let presigning = PresigningConfig::expires_in(Duration::from_secs(expires_in_secs))
            .map_err(|e| ServiceError::S3Error(format!("presign config error: {e}")))?;

        let presigned = self
            .client
            .get_object()
            .bucket(&self.bucket)
            .key(key)
            .presigned(presigning)
            .await
            .map_err(|e| ServiceError::S3Error(format!("presign failed: {e}")))?;

        Ok(presigned.uri().to_string())
    }

    /// Delete an object from S3.
    pub async fn delete_object(&self, key: &str) -> Result<(), ServiceError> {
        self.client
            .delete_object()
            .bucket(&self.bucket)
            .key(key)
            .send()
            .await
            .map_err(|e| ServiceError::S3Error(format!("delete failed: {e}")))?;

        tracing::info!(key = %key, "Deleted object from S3");
        Ok(())
    }

    /// Generate a presigned download URL that instructs S3 to serve the object
    /// with the given content type inline, regardless of the stored object's
    /// Content-Type metadata.
    pub async fn presigned_download_url_with_content_type(
        &self,
        key: &str,
        expires_in_secs: u64,
        content_type: &str,
    ) -> Result<String, ServiceError> {
        let presigning = PresigningConfig::expires_in(Duration::from_secs(expires_in_secs))
            .map_err(|e| ServiceError::S3Error(format!("presign config error: {e}")))?;

        let presigned = self
            .client
            .get_object()
            .bucket(&self.bucket)
            .key(key)
            .response_content_type(content_type)
            .response_content_disposition("inline")
            .presigned(presigning)
            .await
            .map_err(|e| ServiceError::S3Error(format!("presign failed: {e}")))?;

        Ok(presigned.uri().to_string())
    }

    /// Copy an object within S3 (used for versioning).
    pub async fn copy_object(&self, source_key: &str, dest_key: &str) -> Result<(), ServiceError> {
        let copy_source = format!("{}/{}", self.bucket, source_key);
        self.client
            .copy_object()
            .bucket(&self.bucket)
            .copy_source(&copy_source)
            .key(dest_key)
            .send()
            .await
            .map_err(|e| ServiceError::S3Error(format!("copy failed: {e}")))?;

        tracing::info!(source = %source_key, dest = %dest_key, "Copied object in S3");
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::AwsConfig;

    async fn test_client() -> S3Client {
        std::env::set_var("AWS_ACCESS_KEY_ID", "test");
        std::env::set_var("AWS_SECRET_ACCESS_KEY", "test");
        S3Client::new(&AwsConfig {
            region: "us-east-1".to_string(),
            endpoint_url: Some("http://localhost:4566".to_string()),
            s3_bucket: "otterworks-files".to_string(),
            dynamodb_table: "t".to_string(),
            dynamodb_folders_table: "t".to_string(),
            dynamodb_versions_table: "t".to_string(),
            dynamodb_shares_table: "t".to_string(),
        })
        .await
    }

    // AC-03/BDD-03: presigned URL must override the served content type so
    // objects stored without accurate Content-Type metadata still render inline.
    #[tokio::test]
    async fn presigned_url_with_content_type_sets_response_overrides() {
        let s3 = test_client().await;
        let url = s3
            .presigned_download_url_with_content_type("files/u/f", 3600, "application/pdf")
            .await
            .expect("presign should succeed");

        assert!(url.contains("response-content-type=application%2Fpdf"));
        assert!(url.contains("response-content-disposition=inline"));
    }

    #[tokio::test]
    async fn plain_presigned_url_has_no_response_overrides() {
        let s3 = test_client().await;
        let url = s3
            .presigned_download_url("files/u/f", 3600)
            .await
            .expect("presign should succeed");

        assert!(!url.contains("response-content-type"));
    }
}

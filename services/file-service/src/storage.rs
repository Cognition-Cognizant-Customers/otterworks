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
    use crate::test_support::{expected_request, s3_body, s3_client, s3_error, s3_ok, TEST_BUCKET};
    use aws_smithy_http_client::test_util::ReplayEvent;

    const KEY: &str = "files/owner/file-1";

    fn object_uri() -> String {
        format!("http://s3.local/{TEST_BUCKET}/{KEY}")
    }

    #[tokio::test]
    async fn upload_object_puts_body_to_the_configured_bucket() {
        let (s3, http) = s3_client(vec![ReplayEvent::new(
            expected_request(&object_uri()),
            s3_ok(),
        )]);

        s3.upload_object(KEY, Bytes::from_static(b"hello"), "text/plain")
            .await
            .expect("upload should succeed");

        let request = http.actual_requests().next().unwrap();
        assert_eq!(request.method(), "PUT");
        assert!(request.uri().starts_with(&object_uri()));
        assert_eq!(request.headers().get("content-type"), Some("text/plain"));
        assert_eq!(request.body().bytes(), Some(&b"hello"[..]));
    }

    #[tokio::test]
    async fn upload_object_maps_s3_failure_to_service_error() {
        let (s3, _http) = s3_client(vec![ReplayEvent::new(
            expected_request(&object_uri()),
            s3_error(404, "NoSuchBucket"),
        )]);

        let err = s3
            .upload_object(KEY, Bytes::from_static(b"hello"), "text/plain")
            .await
            .expect_err("missing bucket should fail");

        assert!(
            matches!(&err, ServiceError::S3Error(msg) if msg.starts_with("upload failed")),
            "unexpected error: {err:?}"
        );
    }

    #[tokio::test]
    async fn download_object_returns_body_bytes() {
        let (s3, http) = s3_client(vec![ReplayEvent::new(
            expected_request(&object_uri()),
            s3_body("file contents"),
        )]);

        let body = s3.download_object(KEY).await.expect("download should work");

        assert_eq!(body, Bytes::from_static(b"file contents"));
        assert_eq!(http.actual_requests().next().unwrap().method(), "GET");
    }

    #[tokio::test]
    async fn download_object_maps_missing_key_to_service_error() {
        let (s3, _http) = s3_client(vec![ReplayEvent::new(
            expected_request(&object_uri()),
            s3_error(404, "NoSuchKey"),
        )]);

        let err = s3
            .download_object(KEY)
            .await
            .expect_err("missing key should fail");

        assert!(
            matches!(&err, ServiceError::S3Error(msg) if msg.starts_with("download failed")),
            "unexpected error: {err:?}"
        );
    }

    #[tokio::test]
    async fn delete_object_issues_a_delete() {
        let (s3, http) = s3_client(vec![ReplayEvent::new(
            expected_request(&object_uri()),
            http::Response::builder()
                .status(204)
                .body(aws_smithy_types::body::SdkBody::empty())
                .unwrap(),
        )]);

        s3.delete_object(KEY).await.expect("delete should succeed");

        let request = http.actual_requests().next().unwrap();
        assert_eq!(request.method(), "DELETE");
        assert!(request.uri().starts_with(&object_uri()));
    }

    #[tokio::test]
    async fn delete_object_maps_s3_failure_to_service_error() {
        let (s3, _http) = s3_client(vec![ReplayEvent::new(
            expected_request(&object_uri()),
            s3_error(403, "AccessDenied"),
        )]);

        let err = s3
            .delete_object(KEY)
            .await
            .expect_err("access denied should fail");

        assert!(
            matches!(&err, ServiceError::S3Error(msg) if msg.starts_with("delete failed")),
            "unexpected error: {err:?}"
        );
    }

    #[tokio::test]
    async fn copy_object_uses_bucket_qualified_source() {
        let (s3, http) = s3_client(vec![ReplayEvent::new(
            expected_request(&format!("http://s3.local/{TEST_BUCKET}/files/owner/file-2")),
            s3_body("<CopyObjectResult></CopyObjectResult>"),
        )]);

        s3.copy_object(KEY, "files/owner/file-2")
            .await
            .expect("copy should succeed");

        let request = http.actual_requests().next().unwrap();
        assert_eq!(
            request.headers().get("x-amz-copy-source"),
            Some(format!("{TEST_BUCKET}/{KEY}").as_str())
        );
    }

    #[tokio::test]
    async fn presigned_download_url_is_signed_and_offline() {
        // Presigning performs no HTTP call, hence the empty replay list.
        let (s3, http) = s3_client(vec![]);

        let url = s3
            .presigned_download_url(KEY, 900)
            .await
            .expect("presign should succeed");

        assert!(url.starts_with(&object_uri()), "unexpected url: {url}");
        assert!(url.contains("X-Amz-Signature="), "unexpected url: {url}");
        assert!(url.contains("X-Amz-Expires=900"), "unexpected url: {url}");
        assert_eq!(http.actual_requests().count(), 0);
    }
}

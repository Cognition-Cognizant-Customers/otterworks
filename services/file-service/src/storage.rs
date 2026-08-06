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

/// Test-only helpers for building an [`S3Client`] backed by a stubbed AWS SDK
/// client. Nothing here talks to the network (no LocalStack), so the suite is
/// hermetic and order-independent.
#[cfg(test)]
pub(crate) mod test_support {
    use super::S3Client;
    use aws_sdk_s3::config::retry::RetryConfig;
    use aws_smithy_mocks::{mock_client, Rule, RuleMode};

    pub(crate) const TEST_BUCKET: &str = "otterworks-files-test";

    /// Build an `S3Client` driven entirely by `rules`.
    ///
    /// Retries are disabled so a stubbed failure surfaces after exactly one
    /// attempt; the SDK's test defaults pin the time source, which keeps
    /// presigned URLs byte-for-byte reproducible.
    pub(crate) fn stub_s3_with_bucket(bucket: &str, rules: &[&Rule]) -> S3Client {
        let client = mock_client!(aws_sdk_s3, RuleMode::MatchAny, rules, |builder| builder
            .force_path_style(true)
            .retry_config(RetryConfig::disabled()));
        S3Client {
            client,
            bucket: bucket.to_string(),
        }
    }

    pub(crate) fn stub_s3(rules: &[&Rule]) -> S3Client {
        stub_s3_with_bucket(TEST_BUCKET, rules)
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::{stub_s3, stub_s3_with_bucket, TEST_BUCKET};
    use super::*;
    use aws_sdk_s3::operation::copy_object::{CopyObjectError, CopyObjectOutput};
    use aws_sdk_s3::operation::delete_object::{DeleteObjectError, DeleteObjectOutput};
    use aws_sdk_s3::operation::get_object::{GetObjectError, GetObjectOutput};
    use aws_sdk_s3::operation::put_object::{PutObjectError, PutObjectOutput};
    use aws_sdk_s3::types::error::{InvalidObjectState, InvalidRequest, NoSuchKey};
    use aws_smithy_mocks::mock;
    use aws_smithy_types::byte_stream::ByteStream;

    /// `PresigningConfig` rejects anything longer than one week.
    const MAX_PRESIGN_SECS: u64 = 7 * 24 * 60 * 60;

    /// Presigning never transmits, but it still runs the interceptor chain, so
    /// the stub needs a `GetObject` rule present.
    fn presign_rule() -> aws_smithy_mocks::Rule {
        mock!(aws_sdk_s3::Client::get_object).then_output(|| GetObjectOutput::builder().build())
    }

    fn assert_s3_error(err: ServiceError, expected_prefix: &str) {
        match err {
            ServiceError::S3Error(msg) => assert!(
                msg.starts_with(expected_prefix),
                "expected message starting with {expected_prefix:?}, got {msg:?}"
            ),
            other => panic!("expected ServiceError::S3Error, got {other:?}"),
        }
    }

    // ── upload_object ──────────────────────────────────────────────────

    #[tokio::test]
    async fn upload_object_sends_bucket_key_and_content_type() {
        let rule = mock!(aws_sdk_s3::Client::put_object)
            .match_requests(|req| {
                req.bucket() == Some(TEST_BUCKET)
                    && req.key() == Some("files/abc")
                    && req.content_type() == Some("text/plain")
            })
            .then_output(|| PutObjectOutput::builder().build());
        let s3 = stub_s3(&[&rule]);

        s3.upload_object("files/abc", Bytes::from_static(b"hello"), "text/plain")
            .await
            .expect("upload should succeed");

        assert_eq!(rule.num_calls(), 1);
    }

    #[tokio::test]
    async fn upload_object_accepts_zero_byte_body() {
        let rule = mock!(aws_sdk_s3::Client::put_object)
            .then_output(|| PutObjectOutput::builder().build());
        let s3 = stub_s3(&[&rule]);

        s3.upload_object("files/empty", Bytes::new(), "application/octet-stream")
            .await
            .expect("a zero-byte object is a valid S3 object");

        assert_eq!(rule.num_calls(), 1);
    }

    #[tokio::test]
    async fn upload_object_propagates_s3_failure() {
        let rule = mock!(aws_sdk_s3::Client::put_object).then_error(|| {
            PutObjectError::InvalidRequest(
                InvalidRequest::builder()
                    .message("bucket is misconfigured")
                    .build(),
            )
        });
        let s3 = stub_s3(&[&rule]);

        let err = s3
            .upload_object("files/abc", Bytes::from_static(b"hi"), "text/plain")
            .await
            .expect_err("a failing PutObject must surface as an error");

        assert_s3_error(err, "upload failed");
        assert_eq!(
            rule.num_calls(),
            1,
            "retries must not be silently swallowed"
        );
    }

    #[tokio::test]
    async fn upload_object_targets_the_bucket_it_was_constructed_with() {
        let rule = mock!(aws_sdk_s3::Client::put_object)
            .match_requests(|req| req.bucket() == Some("some-other-bucket"))
            .then_output(|| PutObjectOutput::builder().build());
        let s3 = stub_s3_with_bucket("some-other-bucket", &[&rule]);

        s3.upload_object("k", Bytes::from_static(b"x"), "text/plain")
            .await
            .unwrap();

        assert_eq!(rule.num_calls(), 1);
    }

    // ── download_object ────────────────────────────────────────────────

    #[tokio::test]
    async fn download_object_returns_body_bytes() {
        let rule = mock!(aws_sdk_s3::Client::get_object).then_output(|| {
            GetObjectOutput::builder()
                .body(ByteStream::from_static(b"file-contents"))
                .build()
        });
        let s3 = stub_s3(&[&rule]);

        let bytes = s3.download_object("files/abc").await.unwrap();

        assert_eq!(&bytes[..], b"file-contents");
    }

    #[tokio::test]
    async fn download_object_returns_empty_bytes_for_zero_byte_object() {
        let rule = mock!(aws_sdk_s3::Client::get_object).then_output(|| {
            GetObjectOutput::builder()
                .body(ByteStream::from_static(b""))
                .build()
        });
        let s3 = stub_s3(&[&rule]);

        let bytes = s3.download_object("files/empty").await.unwrap();

        assert!(bytes.is_empty());
    }

    #[tokio::test]
    async fn download_object_maps_missing_key_to_s3_error() {
        let rule = mock!(aws_sdk_s3::Client::get_object)
            .then_error(|| GetObjectError::NoSuchKey(NoSuchKey::builder().build()));
        let s3 = stub_s3(&[&rule]);

        let err = s3.download_object("files/missing").await.unwrap_err();

        assert_s3_error(err, "download failed");
    }

    #[tokio::test]
    async fn download_object_maps_archived_object_to_s3_error() {
        let rule = mock!(aws_sdk_s3::Client::get_object).then_error(|| {
            GetObjectError::InvalidObjectState(InvalidObjectState::builder().build())
        });
        let s3 = stub_s3(&[&rule]);

        let err = s3.download_object("files/glacier").await.unwrap_err();

        assert_s3_error(err, "download failed");
    }

    // ── presigned_download_url ─────────────────────────────────────────

    #[tokio::test]
    async fn presigned_download_url_contains_bucket_key_and_expiry() {
        let rule = presign_rule();
        let s3 = stub_s3(&[&rule]);

        let url = s3.presigned_download_url("files/abc", 3600).await.unwrap();

        assert!(url.contains(TEST_BUCKET), "url should be path-style: {url}");
        assert!(url.contains("files/abc"), "url should carry the key: {url}");
        assert!(
            url.contains("X-Amz-Expires=3600"),
            "url should carry the expiry: {url}"
        );
        assert!(
            url.contains("X-Amz-Signature="),
            "url should be signed: {url}"
        );
    }

    #[tokio::test]
    async fn presigned_download_url_is_deterministic_for_the_same_inputs() {
        let rule = presign_rule();
        let s3 = stub_s3(&[&rule]);

        let first = s3.presigned_download_url("files/abc", 3600).await.unwrap();
        let second = s3.presigned_download_url("files/abc", 3600).await.unwrap();

        assert_eq!(first, second);
    }

    /// Boundary trio around the SDK's one-week presign ceiling.
    #[tokio::test]
    async fn presigned_download_url_just_under_the_one_week_ceiling_is_accepted() {
        let rule = presign_rule();
        let s3 = stub_s3(&[&rule]);
        let url = s3
            .presigned_download_url("files/abc", MAX_PRESIGN_SECS - 1)
            .await
            .unwrap();
        assert!(url.contains(&format!("X-Amz-Expires={}", MAX_PRESIGN_SECS - 1)));
    }

    #[tokio::test]
    async fn presigned_download_url_exactly_at_the_one_week_ceiling_is_accepted() {
        let rule = presign_rule();
        let s3 = stub_s3(&[&rule]);
        let url = s3
            .presigned_download_url("files/abc", MAX_PRESIGN_SECS)
            .await
            .unwrap();
        assert!(url.contains(&format!("X-Amz-Expires={MAX_PRESIGN_SECS}")));
    }

    #[tokio::test]
    async fn presigned_download_url_over_the_one_week_ceiling_is_rejected() {
        let rule = presign_rule();
        let s3 = stub_s3(&[&rule]);

        let err = s3
            .presigned_download_url("files/abc", MAX_PRESIGN_SECS + 1)
            .await
            .unwrap_err();

        assert_s3_error(err, "presign config error");
    }

    #[tokio::test]
    async fn presigned_download_url_percent_encodes_unicode_keys() {
        let rule = presign_rule();
        let s3 = stub_s3(&[&rule]);

        let url = s3
            .presigned_download_url("files/naïve-résumé.txt", 60)
            .await
            .unwrap();

        assert!(
            !url.contains('ï') && url.contains("na%C3%AFve"),
            "unicode key must be percent-encoded: {url}"
        );
    }

    // ── delete_object ──────────────────────────────────────────────────

    #[tokio::test]
    async fn delete_object_sends_bucket_and_key() {
        let rule = mock!(aws_sdk_s3::Client::delete_object)
            .match_requests(|req| {
                req.bucket() == Some(TEST_BUCKET) && req.key() == Some("files/abc")
            })
            .then_output(|| DeleteObjectOutput::builder().build());
        let s3 = stub_s3(&[&rule]);

        s3.delete_object("files/abc").await.unwrap();

        assert_eq!(rule.num_calls(), 1);
    }

    #[tokio::test]
    async fn delete_object_propagates_s3_failure() {
        let rule = mock!(aws_sdk_s3::Client::delete_object)
            .then_error(|| DeleteObjectError::unhandled("access denied"));
        let s3 = stub_s3(&[&rule]);

        let err = s3.delete_object("files/abc").await.unwrap_err();

        assert_s3_error(err, "delete failed");
    }

    // ── copy_object ────────────────────────────────────────────────────

    #[tokio::test]
    async fn copy_object_builds_copy_source_from_bucket_and_source_key() {
        let rule = mock!(aws_sdk_s3::Client::copy_object)
            .match_requests(|req| {
                req.copy_source() == Some(&format!("{TEST_BUCKET}/files/v1"))
                    && req.key() == Some("files/v2")
                    && req.bucket() == Some(TEST_BUCKET)
            })
            .then_output(|| CopyObjectOutput::builder().build());
        let s3 = stub_s3(&[&rule]);

        s3.copy_object("files/v1", "files/v2").await.unwrap();

        assert_eq!(rule.num_calls(), 1);
    }

    #[tokio::test]
    async fn copy_object_propagates_s3_failure() {
        let rule = mock!(aws_sdk_s3::Client::copy_object)
            .then_error(|| CopyObjectError::unhandled("source missing"));
        let s3 = stub_s3(&[&rule]);

        let err = s3.copy_object("files/v1", "files/v2").await.unwrap_err();

        assert_s3_error(err, "copy failed");
    }
}

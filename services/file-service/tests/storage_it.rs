//! Integration tests for the S3 layer (`src/storage.rs`).
//!
//! The S3 client is pointed at the in-process mock in `tests/common`, so every AWS error
//! variant can be provoked without LocalStack. Each test asserts both the `ServiceError`
//! variant and the HTTP status it renders as, since that mapping is what callers see.
#![allow(dead_code)]

#[path = "../src/config.rs"]
mod config;
#[path = "../src/errors.rs"]
mod errors;
#[path = "../src/storage.rs"]
mod storage;

mod common;

use std::sync::Arc;

use actix_web::ResponseError;
use bytes::Bytes;

use common::{s3_error, s3_ok, MockAws, MockResponse, Responder, TEST_BUCKET};
use errors::ServiceError;

const MAX_PRESIGN_SECS: u64 = 604_800; // one week, enforced by PresigningConfig

async fn mock_s3(responder: Responder) -> (MockAws, storage::S3Client) {
    let aws = MockAws::start(responder).await;
    let client = storage::S3Client {
        client: common::s3_sdk_client(&aws.endpoint()),
        bucket: TEST_BUCKET.to_string(),
    };
    (aws, client)
}

fn always(response: fn() -> MockResponse) -> Responder {
    Arc::new(move |_req, _index| response())
}

fn body_responder(body: &'static str) -> Responder {
    Arc::new(move |_req, _index| MockResponse {
        status: 200,
        content_type: "application/octet-stream",
        body: body.to_string(),
    })
}

fn status(err: &ServiceError) -> u16 {
    err.error_response().status().as_u16()
}

// ── upload_object ──────────────────────────────────────────────────────

#[actix_web::test]
async fn upload_object_puts_the_body_at_the_bucket_scoped_key() {
    let (aws, s3) = mock_s3(always(s3_ok)).await;

    let result = s3
        .upload_object("files/a/b", Bytes::from_static(b"hello"), "text/plain")
        .await;

    assert!(result.is_ok());
    let calls = aws.s3_calls("PUT");
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].path_only(), "/test-bucket/files/a/b");
    assert_eq!(
        calls[0].headers.get("content-type").map(String::as_str),
        Some("text/plain")
    );
    assert_eq!(calls[0].body, b"hello");
}

#[actix_web::test]
async fn upload_object_accepts_an_empty_body() {
    let (aws, s3) = mock_s3(always(s3_ok)).await;

    let result = s3
        .upload_object("files/a/empty", Bytes::new(), "application/octet-stream")
        .await;

    assert!(result.is_ok());
    assert!(aws.s3_calls("PUT")[0].body.is_empty());
}

#[actix_web::test]
async fn upload_object_maps_a_server_error_to_s3error_500() {
    let (_aws, s3) = mock_s3(always(|| s3_error(500, "InternalError"))).await;

    let err = s3
        .upload_object("files/a/b", Bytes::from_static(b"hello"), "text/plain")
        .await
        .expect_err("expected the upload to fail");

    assert!(matches!(err, ServiceError::S3Error(_)));
    assert!(err.to_string().contains("upload failed"));
    assert_eq!(status(&err), 500);
}

#[actix_web::test]
async fn upload_object_maps_no_such_bucket_to_s3error_500() {
    let (_aws, s3) = mock_s3(always(|| s3_error(404, "NoSuchBucket"))).await;

    let err = s3
        .upload_object("files/a/b", Bytes::from_static(b"hello"), "text/plain")
        .await
        .expect_err("expected the upload to fail");

    assert!(matches!(err, ServiceError::S3Error(_)));
    assert_eq!(status(&err), 500);
}

#[actix_web::test]
async fn upload_object_maps_access_denied_to_s3error_500() {
    let (_aws, s3) = mock_s3(always(|| s3_error(403, "AccessDenied"))).await;

    let err = s3
        .upload_object("files/a/b", Bytes::from_static(b"hello"), "text/plain")
        .await
        .expect_err("expected the upload to fail");

    assert_eq!(status(&err), 500);
}

// ── download_object ────────────────────────────────────────────────────

#[actix_web::test]
async fn download_object_returns_the_body_bytes() {
    let (aws, s3) = mock_s3(body_responder("otter bytes")).await;

    let bytes = s3.download_object("files/a/b").await.expect("download");

    assert_eq!(&bytes[..], b"otter bytes");
    assert_eq!(aws.s3_calls("GET")[0].path_only(), "/test-bucket/files/a/b");
}

#[actix_web::test]
async fn download_object_returns_an_empty_body_as_empty_bytes() {
    let (_aws, s3) = mock_s3(body_responder("")).await;

    let bytes = s3.download_object("files/a/empty").await.expect("download");

    assert!(bytes.is_empty());
}

#[actix_web::test]
async fn download_object_maps_no_such_key_to_s3error_500_not_404() {
    // Pins today's behaviour: every S3 failure collapses into a 500 `storage_error`, so a
    // missing blob is reported as a server fault rather than a 404 (FINDING-9).
    let (_aws, s3) = mock_s3(always(|| s3_error(404, "NoSuchKey"))).await;

    let err = s3
        .download_object("files/a/missing")
        .await
        .expect_err("expected the download to fail");

    assert!(matches!(err, ServiceError::S3Error(_)));
    assert!(err.to_string().contains("download failed"));
    assert_eq!(status(&err), 500);
}

#[actix_web::test]
async fn download_object_maps_a_server_error_to_s3error_500() {
    let (_aws, s3) = mock_s3(always(|| s3_error(503, "SlowDown"))).await;

    let err = s3
        .download_object("files/a/b")
        .await
        .expect_err("expected the download to fail");

    assert_eq!(status(&err), 500);
}

// ── delete_object ──────────────────────────────────────────────────────

#[actix_web::test]
async fn delete_object_issues_a_delete_for_the_key() {
    let (aws, s3) = mock_s3(always(|| MockResponse {
        status: 204,
        content_type: "application/xml",
        body: String::new(),
    }))
    .await;

    s3.delete_object("files/a/b").await.expect("delete");

    let calls = aws.s3_calls("DELETE");
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].path_only(), "/test-bucket/files/a/b");
}

#[actix_web::test]
async fn delete_object_maps_access_denied_to_s3error_500() {
    let (_aws, s3) = mock_s3(always(|| s3_error(403, "AccessDenied"))).await;

    let err = s3
        .delete_object("files/a/b")
        .await
        .expect_err("expected the delete to fail");

    assert!(matches!(err, ServiceError::S3Error(_)));
    assert!(err.to_string().contains("delete failed"));
    assert_eq!(status(&err), 500);
}

// ── copy_object ────────────────────────────────────────────────────────

#[actix_web::test]
async fn copy_object_sends_a_bucket_qualified_copy_source() {
    let (aws, s3) = mock_s3(Arc::new(|_req, _i| MockResponse {
        status: 200,
        content_type: "application/xml",
        body: "<CopyObjectResult><ETag>\"etag\"</ETag></CopyObjectResult>".into(),
    }))
    .await;

    s3.copy_object("files/a/v1", "files/a/v2")
        .await
        .expect("copy");

    let calls = aws.s3_calls("PUT");
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].path_only(), "/test-bucket/files/a/v2");
    assert_eq!(
        calls[0]
            .headers
            .get("x-amz-copy-source")
            .map(String::as_str),
        Some("test-bucket/files/a/v1")
    );
}

#[actix_web::test]
async fn copy_object_maps_a_missing_source_to_s3error_500() {
    let (_aws, s3) = mock_s3(always(|| s3_error(404, "NoSuchKey"))).await;

    let err = s3
        .copy_object("files/a/missing", "files/a/v2")
        .await
        .expect_err("expected the copy to fail");

    assert!(matches!(err, ServiceError::S3Error(_)));
    assert!(err.to_string().contains("copy failed"));
    assert_eq!(status(&err), 500);
}

// ── presigned_download_url ─────────────────────────────────────────────

#[actix_web::test]
async fn presigned_download_url_signs_locally_without_calling_s3() {
    let (aws, s3) = mock_s3(always(s3_ok)).await;

    let url = s3
        .presigned_download_url("files/a/b", 3600)
        .await
        .expect("presign");

    assert!(url.starts_with(&format!("{}/test-bucket/files/a/b", aws.endpoint())));
    assert!(url.contains("X-Amz-Expires=3600"));
    assert!(url.contains("X-Amz-Signature="));
    assert!(url.contains("X-Amz-Credential=test-access-key"));
    assert!(aws.requests().is_empty(), "presigning must not hit S3");
}

#[actix_web::test]
async fn presigned_download_url_accepts_a_zero_second_expiry() {
    let (_aws, s3) = mock_s3(always(s3_ok)).await;

    let url = s3
        .presigned_download_url("files/a/b", 0)
        .await
        .expect("presign");

    assert!(url.contains("X-Amz-Expires=0"));
}

// The presign expiry ceiling is one week; boundary trio around it.

#[actix_web::test]
async fn presigned_download_url_one_second_under_the_ceiling_is_accepted() {
    let (_aws, s3) = mock_s3(always(s3_ok)).await;

    let url = s3
        .presigned_download_url("files/a/b", MAX_PRESIGN_SECS - 1)
        .await
        .expect("presign");

    assert!(url.contains(&format!("X-Amz-Expires={}", MAX_PRESIGN_SECS - 1)));
}

#[actix_web::test]
async fn presigned_download_url_exactly_at_the_ceiling_is_accepted() {
    let (_aws, s3) = mock_s3(always(s3_ok)).await;

    let url = s3
        .presigned_download_url("files/a/b", MAX_PRESIGN_SECS)
        .await
        .expect("presign");

    assert!(url.contains(&format!("X-Amz-Expires={MAX_PRESIGN_SECS}")));
}

#[actix_web::test]
async fn presigned_download_url_one_second_over_the_ceiling_is_rejected() {
    let (_aws, s3) = mock_s3(always(s3_ok)).await;

    let err = s3
        .presigned_download_url("files/a/b", MAX_PRESIGN_SECS + 1)
        .await
        .expect_err("expected the presign config to be rejected");

    assert!(matches!(err, ServiceError::S3Error(_)));
    assert!(err.to_string().contains("presign config error"));
    assert_eq!(status(&err), 500);
}

#[actix_web::test]
async fn presigned_download_url_encodes_keys_with_spaces_and_unicode() {
    let (_aws, s3) = mock_s3(always(s3_ok)).await;

    let url = s3
        .presigned_download_url("files/a/my résumé.txt", 60)
        .await
        .expect("presign");

    assert!(!url.contains(' '));
    assert!(url.contains("my%20r%C3%A9sum%C3%A9.txt"));
}

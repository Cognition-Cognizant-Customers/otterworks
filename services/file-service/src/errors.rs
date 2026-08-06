use actix_web::{HttpResponse, ResponseError};
use std::fmt;

#[derive(Debug, thiserror::Error)]
pub enum ServiceError {
    #[error("File not found: {0}")]
    FileNotFound(String),

    #[error("Folder not found: {0}")]
    FolderNotFound(String),

    #[error("Version not found: {0}")]
    VersionNotFound(String),

    #[error("Share not found: {0}")]
    ShareNotFound(String),

    #[error("Bad request: {0}")]
    BadRequest(String),

    #[error("File too large: max {max_bytes} bytes, got {actual_bytes} bytes")]
    FileTooLarge { max_bytes: u64, actual_bytes: u64 },

    #[error("Unauthorized: {0}")]
    Unauthorized(String),

    #[error("Forbidden: {0}")]
    Forbidden(String),

    #[error("S3 error: {0}")]
    S3Error(String),

    #[error("DynamoDB error: {0}")]
    DynamoError(String),

    #[error("SNS error: {0}")]
    SnsError(String),

    #[error("Internal error: {0}")]
    Internal(String),
}

impl ResponseError for ServiceError {
    fn error_response(&self) -> HttpResponse {
        let (status, error_type) = match self {
            ServiceError::FileNotFound(_) => {
                (actix_web::http::StatusCode::NOT_FOUND, "file_not_found")
            }
            ServiceError::FolderNotFound(_) => {
                (actix_web::http::StatusCode::NOT_FOUND, "folder_not_found")
            }
            ServiceError::VersionNotFound(_) => {
                (actix_web::http::StatusCode::NOT_FOUND, "version_not_found")
            }
            ServiceError::ShareNotFound(_) => {
                (actix_web::http::StatusCode::NOT_FOUND, "share_not_found")
            }
            ServiceError::BadRequest(_) => {
                (actix_web::http::StatusCode::BAD_REQUEST, "bad_request")
            }
            ServiceError::FileTooLarge { .. } => (
                actix_web::http::StatusCode::PAYLOAD_TOO_LARGE,
                "file_too_large",
            ),
            ServiceError::Unauthorized(_) => {
                (actix_web::http::StatusCode::UNAUTHORIZED, "unauthorized")
            }
            ServiceError::Forbidden(_) => (actix_web::http::StatusCode::FORBIDDEN, "forbidden"),
            ServiceError::S3Error(_) => (
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                "storage_error",
            ),
            ServiceError::DynamoError(_) => (
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                "metadata_error",
            ),
            ServiceError::SnsError(_) => (
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                "event_error",
            ),
            ServiceError::Internal(_) => (
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                "internal_error",
            ),
        };

        HttpResponse::build(status).json(ErrorResponse {
            error: error_type.to_string(),
            message: self.to_string(),
        })
    }
}

#[derive(Debug, serde::Serialize)]
pub struct ErrorResponse {
    pub error: String,
    pub message: String,
}

impl fmt::Display for ErrorResponse {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.error, self.message)
    }
}

/// WP-02 — status-code mapping for every `ServiceError` variant.
#[cfg(test)]
mod status_mapping_tests {
    use super::*;
    use actix_web::body::to_bytes;
    use actix_web::http::StatusCode;

    /// Every variant, the status it must map to, and the machine-readable
    /// `error` discriminator clients switch on. Adding a variant without
    /// adding it here leaves `exhaustive_variant_coverage` failing.
    fn mapping_table() -> Vec<(ServiceError, StatusCode, &'static str)> {
        vec![
            (
                ServiceError::FileNotFound("file-1".into()),
                StatusCode::NOT_FOUND,
                "file_not_found",
            ),
            (
                ServiceError::FolderNotFound("folder-1".into()),
                StatusCode::NOT_FOUND,
                "folder_not_found",
            ),
            (
                ServiceError::VersionNotFound("version-1".into()),
                StatusCode::NOT_FOUND,
                "version_not_found",
            ),
            (
                ServiceError::ShareNotFound("share-1".into()),
                StatusCode::NOT_FOUND,
                "share_not_found",
            ),
            (
                ServiceError::BadRequest("missing name".into()),
                StatusCode::BAD_REQUEST,
                "bad_request",
            ),
            (
                ServiceError::FileTooLarge {
                    max_bytes: 100,
                    actual_bytes: 101,
                },
                StatusCode::PAYLOAD_TOO_LARGE,
                "file_too_large",
            ),
            (
                ServiceError::Unauthorized("no token".into()),
                StatusCode::UNAUTHORIZED,
                "unauthorized",
            ),
            (
                ServiceError::Forbidden("not the owner".into()),
                StatusCode::FORBIDDEN,
                "forbidden",
            ),
            (
                ServiceError::S3Error("bucket unreachable".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "storage_error",
            ),
            (
                ServiceError::DynamoError("table throttled".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "metadata_error",
            ),
            (
                ServiceError::SnsError("topic missing".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "event_error",
            ),
            (
                ServiceError::Internal("boom".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal_error",
            ),
        ]
    }

    async fn body_json(error: &ServiceError) -> serde_json::Value {
        let response = error.error_response();
        let bytes = to_bytes(response.into_body())
            .await
            .expect("error body is finite");
        serde_json::from_slice(&bytes).expect("error body is JSON")
    }

    #[actix_web::test]
    async fn every_variant_maps_to_its_status_and_error_type() {
        for (error, expected_status, expected_type) in mapping_table() {
            let response = error.error_response();
            assert_eq!(response.status(), expected_status, "status for {error:?}");

            let body = body_json(&error).await;
            assert_eq!(body["error"], expected_type, "error type for {error:?}");
            assert_eq!(body["message"], error.to_string(), "message for {error:?}");
        }
    }

    /// WP-02 finding F6 (genuine defect, pinned not fixed).
    ///
    /// `ResponseError::status_code()` is left at its default, which is a flat
    /// `500`, while `error_response()` carries the real status. The wire
    /// behaviour is correct because actix builds the response from
    /// `error_response()`, but any caller that asks the error for its status -
    /// including `ResponseError`'s own default `error_response()`, error
    /// handlers and status-based metrics - is told every failure is a 500.
    #[test]
    #[ignore = "expected-fail, WP-02 finding F6: ResponseError::status_code() is not overridden"]
    fn status_code_agrees_with_error_response() {
        for (error, expected_status, _) in mapping_table() {
            assert_eq!(
                error.status_code(),
                expected_status,
                "status_code() for {error:?}"
            );
        }
    }

    /// Pins today's behaviour of the same defect so the discrepancy is
    /// visible in a green run.
    #[test]
    fn status_code_currently_reports_500_for_every_variant() {
        for (error, expected_status, _) in mapping_table() {
            assert_eq!(
                error.status_code(),
                StatusCode::INTERNAL_SERVER_ERROR,
                "unexpected status_code() for {error:?}"
            );
            if expected_status != StatusCode::INTERNAL_SERVER_ERROR {
                assert_ne!(
                    error.status_code(),
                    error.error_response().status(),
                    "{error:?} should still disagree with itself"
                );
            }
        }
    }

    #[test]
    fn exhaustive_variant_coverage() {
        // Compile-time reminder: extend `mapping_table` when a variant is
        // added. The match is exhaustive, so a new variant breaks the build
        // here rather than silently shipping an untested mapping.
        fn discriminator(error: &ServiceError) -> &'static str {
            match error {
                ServiceError::FileNotFound(_) => "file_not_found",
                ServiceError::FolderNotFound(_) => "folder_not_found",
                ServiceError::VersionNotFound(_) => "version_not_found",
                ServiceError::ShareNotFound(_) => "share_not_found",
                ServiceError::BadRequest(_) => "bad_request",
                ServiceError::FileTooLarge { .. } => "file_too_large",
                ServiceError::Unauthorized(_) => "unauthorized",
                ServiceError::Forbidden(_) => "forbidden",
                ServiceError::S3Error(_) => "storage_error",
                ServiceError::DynamoError(_) => "metadata_error",
                ServiceError::SnsError(_) => "event_error",
                ServiceError::Internal(_) => "internal_error",
            }
        }

        let table = mapping_table();
        assert_eq!(table.len(), 12, "one row per variant");
        for (error, _, expected_type) in &table {
            assert_eq!(&discriminator(error), expected_type);
        }
    }

    #[test]
    fn the_four_not_found_variants_stay_distinguishable() {
        let not_found: Vec<&str> = mapping_table()
            .iter()
            .filter(|(_, status, _)| *status == StatusCode::NOT_FOUND)
            .map(|(_, _, error_type)| *error_type)
            .collect();

        assert_eq!(not_found.len(), 4);
        let mut unique = not_found.clone();
        unique.sort_unstable();
        unique.dedup();
        assert_eq!(
            unique.len(),
            4,
            "each 404 needs its own discriminator: {not_found:?}"
        );
    }

    #[actix_web::test]
    async fn error_responses_are_json() {
        for (error, _, _) in mapping_table() {
            let response = error.error_response();
            let content_type = response
                .headers()
                .get("content-type")
                .and_then(|v| v.to_str().ok())
                .unwrap_or_default()
                .to_string();
            assert!(
                content_type.starts_with("application/json"),
                "{error:?} replied with {content_type}"
            );
        }
    }

    #[actix_web::test]
    async fn the_error_body_carries_only_error_and_message() {
        let body = body_json(&ServiceError::BadRequest("missing name".into())).await;
        let object = body.as_object().expect("object body");

        assert_eq!(object.len(), 2, "unexpected fields in {body}");
        assert!(object.contains_key("error") && object.contains_key("message"));
    }

    #[test]
    fn messages_identify_the_missing_resource() {
        assert_eq!(
            ServiceError::FileNotFound("file-1".into()).to_string(),
            "File not found: file-1"
        );
        assert_eq!(
            ServiceError::FolderNotFound("folder-1".into()).to_string(),
            "Folder not found: folder-1"
        );
        assert_eq!(
            ServiceError::VersionNotFound("v7".into()).to_string(),
            "Version not found: v7"
        );
        assert_eq!(
            ServiceError::ShareNotFound("share-1".into()).to_string(),
            "Share not found: share-1"
        );
    }

    #[test]
    fn file_too_large_reports_both_sizes_across_the_limit_boundary() {
        // The limit itself is enforced in handlers.rs (WP-01); what this
        // package owns is that the rendered message is unambiguous at
        // limit-1, limit and limit+1.
        for actual in [99u64, 100, 101] {
            let message = ServiceError::FileTooLarge {
                max_bytes: 100,
                actual_bytes: actual,
            }
            .to_string();
            assert_eq!(
                message,
                format!("File too large: max 100 bytes, got {actual} bytes")
            );
        }
    }

    #[test]
    fn file_too_large_survives_a_saturated_limit() {
        let message = ServiceError::FileTooLarge {
            max_bytes: u64::MAX,
            actual_bytes: u64::MAX,
        }
        .to_string();

        assert!(message.contains(&u64::MAX.to_string()), "{message}");
    }

    /// WP-02 finding F7 (genuine, low severity, pinned not fixed): the detail
    /// of a backend failure is copied verbatim into the 500 body, so internal
    /// table names and driver text reach the client.
    #[actix_web::test]
    async fn internal_failures_echo_backend_detail_to_the_client() {
        let body = body_json(&ServiceError::DynamoError(
            "table otterworks-file-metadata throttled".into(),
        ))
        .await;

        assert!(
            body["message"]
                .as_str()
                .expect("message")
                .contains("otterworks-file-metadata"),
            "internal detail is not redacted: {body}"
        );
    }

    #[test]
    fn error_response_renders_as_error_then_message() {
        let rendered = ErrorResponse {
            error: "bad_request".into(),
            message: "missing name".into(),
        }
        .to_string();

        assert_eq!(rendered, "bad_request: missing name");
    }

    #[test]
    fn an_empty_detail_string_still_renders() {
        assert_eq!(
            ServiceError::BadRequest(String::new()).to_string(),
            "Bad request: "
        );
    }
}

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

#[cfg(test)]
mod tests {
    use super::*;
    use actix_web::body::to_bytes;
    use actix_web::http::StatusCode;

    /// Every variant of `ServiceError`, built with a recognizable payload.
    fn all_variants() -> Vec<ServiceError> {
        vec![
            ServiceError::FileNotFound("file-1".into()),
            ServiceError::FolderNotFound("folder-1".into()),
            ServiceError::VersionNotFound("v7".into()),
            ServiceError::ShareNotFound("share-1".into()),
            ServiceError::BadRequest("invalid file id".into()),
            ServiceError::FileTooLarge {
                max_bytes: 100,
                actual_bytes: 101,
            },
            ServiceError::Unauthorized("no token".into()),
            ServiceError::Forbidden("not the owner".into()),
            ServiceError::S3Error("NoSuchBucket".into()),
            ServiceError::DynamoError("ProvisionedThroughputExceeded".into()),
            ServiceError::SnsError("InvalidParameter".into()),
            ServiceError::Internal("boom".into()),
        ]
    }

    async fn body_of(err: &ServiceError) -> serde_json::Value {
        let resp = err.error_response();
        let bytes = to_bytes(resp.into_body()).await.expect("body is readable");
        serde_json::from_slice(&bytes).expect("error body is JSON")
    }

    #[test]
    fn every_variant_maps_to_its_documented_status_and_error_type() {
        let expected: Vec<(ServiceError, StatusCode, &str)> = vec![
            (
                ServiceError::FileNotFound("f".into()),
                StatusCode::NOT_FOUND,
                "file_not_found",
            ),
            (
                ServiceError::FolderNotFound("f".into()),
                StatusCode::NOT_FOUND,
                "folder_not_found",
            ),
            (
                ServiceError::VersionNotFound("f".into()),
                StatusCode::NOT_FOUND,
                "version_not_found",
            ),
            (
                ServiceError::ShareNotFound("f".into()),
                StatusCode::NOT_FOUND,
                "share_not_found",
            ),
            (
                ServiceError::BadRequest("f".into()),
                StatusCode::BAD_REQUEST,
                "bad_request",
            ),
            (
                ServiceError::FileTooLarge {
                    max_bytes: 1,
                    actual_bytes: 2,
                },
                StatusCode::PAYLOAD_TOO_LARGE,
                "file_too_large",
            ),
            (
                ServiceError::Unauthorized("f".into()),
                StatusCode::UNAUTHORIZED,
                "unauthorized",
            ),
            (
                ServiceError::Forbidden("f".into()),
                StatusCode::FORBIDDEN,
                "forbidden",
            ),
            (
                ServiceError::S3Error("f".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "storage_error",
            ),
            (
                ServiceError::DynamoError("f".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "metadata_error",
            ),
            (
                ServiceError::SnsError("f".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "event_error",
            ),
            (
                ServiceError::Internal("f".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal_error",
            ),
        ];

        assert_eq!(
            expected.len(),
            all_variants().len(),
            "a new ServiceError variant needs a status-code case here"
        );

        for (err, status, error_type) in &expected {
            let resp = err.error_response();
            assert_eq!(resp.status(), *status, "unexpected status for {err:?}");
            assert!(
                !error_type.is_empty(),
                "every variant declares an error type"
            );
        }
    }

    #[test]
    fn status_code_trait_method_reports_500_for_every_variant() {
        // Documents current behavior: only `error_response` is overridden, so
        // the `status_code` half of the `ResponseError` contract still returns
        // the trait default. See FINDING in
        // status_code_should_agree_with_error_response.
        for err in all_variants() {
            assert_eq!(err.status_code(), StatusCode::INTERNAL_SERVER_ERROR);
        }
    }

    #[test]
    #[ignore = "FINDING: ServiceError overrides ResponseError::error_response but not \
                status_code, so ServiceError::status_code() answers 500 for every variant \
                including FileNotFound and BadRequest; any caller or middleware that inspects \
                the error rather than the built response sees the wrong class"]
    fn status_code_should_agree_with_error_response() {
        for err in all_variants() {
            assert_eq!(
                err.status_code(),
                err.error_response().status(),
                "status_code() disagrees with error_response() for {err:?}"
            );
        }
    }

    #[actix_rt::test]
    async fn every_variant_serializes_to_the_error_message_envelope() {
        for err in all_variants() {
            let body = body_of(&err).await;
            let object = body.as_object().expect("body is a JSON object");
            assert_eq!(
                object.len(),
                2,
                "body for {err:?} must have exactly `error` and `message`: {body}"
            );
            assert!(object["error"].is_string(), "error is a string: {body}");
            assert!(object["message"].is_string(), "message is a string: {body}");
            assert_eq!(
                object["message"].as_str().unwrap(),
                err.to_string(),
                "message mirrors Display"
            );
            assert!(
                !object["error"].as_str().unwrap().is_empty(),
                "error type is never blank"
            );
        }
    }

    #[actix_rt::test]
    async fn error_type_strings_are_stable_and_snake_case() {
        let cases = [
            (ServiceError::FileNotFound("x".into()), "file_not_found"),
            (ServiceError::FolderNotFound("x".into()), "folder_not_found"),
            (
                ServiceError::VersionNotFound("x".into()),
                "version_not_found",
            ),
            (ServiceError::ShareNotFound("x".into()), "share_not_found"),
            (ServiceError::BadRequest("x".into()), "bad_request"),
            (
                ServiceError::FileTooLarge {
                    max_bytes: 0,
                    actual_bytes: 1,
                },
                "file_too_large",
            ),
            (ServiceError::Unauthorized("x".into()), "unauthorized"),
            (ServiceError::Forbidden("x".into()), "forbidden"),
            (ServiceError::S3Error("x".into()), "storage_error"),
            (ServiceError::DynamoError("x".into()), "metadata_error"),
            (ServiceError::SnsError("x".into()), "event_error"),
            (ServiceError::Internal("x".into()), "internal_error"),
        ];
        for (err, expected) in cases {
            let body = body_of(&err).await;
            assert_eq!(body["error"], expected);
        }
    }

    #[actix_rt::test]
    async fn error_response_is_json_content_type() {
        for err in all_variants() {
            let resp = err.error_response();
            let content_type = resp
                .headers()
                .get("content-type")
                .and_then(|v| v.to_str().ok())
                .unwrap_or_default()
                .to_string();
            assert!(
                content_type.starts_with("application/json"),
                "{err:?} responded with content-type {content_type:?}"
            );
        }
    }

    #[test]
    fn display_messages_include_their_payload() {
        assert_eq!(
            ServiceError::FileNotFound("abc".into()).to_string(),
            "File not found: abc"
        );
        assert_eq!(
            ServiceError::FolderNotFound("abc".into()).to_string(),
            "Folder not found: abc"
        );
        assert_eq!(
            ServiceError::VersionNotFound("3".into()).to_string(),
            "Version not found: 3"
        );
        assert_eq!(
            ServiceError::ShareNotFound("abc".into()).to_string(),
            "Share not found: abc"
        );
        assert_eq!(
            ServiceError::BadRequest("nope".into()).to_string(),
            "Bad request: nope"
        );
        assert_eq!(
            ServiceError::Unauthorized("nope".into()).to_string(),
            "Unauthorized: nope"
        );
        assert_eq!(
            ServiceError::Forbidden("nope".into()).to_string(),
            "Forbidden: nope"
        );
        assert_eq!(
            ServiceError::S3Error("nope".into()).to_string(),
            "S3 error: nope"
        );
        assert_eq!(
            ServiceError::DynamoError("nope".into()).to_string(),
            "DynamoDB error: nope"
        );
        assert_eq!(
            ServiceError::SnsError("nope".into()).to_string(),
            "SNS error: nope"
        );
        assert_eq!(
            ServiceError::Internal("nope".into()).to_string(),
            "Internal error: nope"
        );
    }

    #[test]
    fn file_too_large_message_reports_both_boundaries() {
        // The handler compares `len > max`, so `max` itself is legal and only
        // `max + 1` produces this error; the message must carry both numbers.
        let err = ServiceError::FileTooLarge {
            max_bytes: 104_857_600,
            actual_bytes: 104_857_601,
        };
        assert_eq!(
            err.to_string(),
            "File too large: max 104857600 bytes, got 104857601 bytes"
        );

        let zero_limit = ServiceError::FileTooLarge {
            max_bytes: 0,
            actual_bytes: 1,
        };
        assert_eq!(
            zero_limit.to_string(),
            "File too large: max 0 bytes, got 1 bytes"
        );

        let huge = ServiceError::FileTooLarge {
            max_bytes: u64::MAX - 1,
            actual_bytes: u64::MAX,
        };
        assert!(huge.to_string().contains(&u64::MAX.to_string()));
    }

    #[test]
    fn error_response_struct_display_joins_type_and_message() {
        let rendered = ErrorResponse {
            error: "file_not_found".into(),
            message: "File not found: abc".into(),
        }
        .to_string();
        assert_eq!(rendered, "file_not_found: File not found: abc");
    }

    #[test]
    fn error_response_struct_serializes_with_stable_field_names() {
        let json = serde_json::to_value(ErrorResponse {
            error: "bad_request".into(),
            message: "invalid file id".into(),
        })
        .unwrap();
        assert_eq!(json["error"], "bad_request");
        assert_eq!(json["message"], "invalid file id");
        assert_eq!(json.as_object().unwrap().len(), 2);
    }

    #[actix_rt::test]
    async fn internal_errors_echo_backend_detail_to_the_client() {
        // Documents current behavior: the raw AWS/S3/DynamoDB error string is
        // returned in the 500 body. See FINDING in
        // internal_errors_should_not_leak_backend_detail.
        let err = ServiceError::DynamoError(
            "dispatch failure: io error: connecting to dynamodb.internal:8000 refused".into(),
        );
        let body = body_of(&err).await;
        assert!(body["message"]
            .as_str()
            .unwrap()
            .contains("dynamodb.internal:8000"));
    }

    #[actix_rt::test]
    #[ignore = "FINDING: 5xx responses serialize the raw backend error (bucket names, internal \
                hostnames, DynamoDB throughput detail) into the client-visible `message` field; \
                only the log should carry it"]
    async fn internal_errors_should_not_leak_backend_detail() {
        for err in [
            ServiceError::S3Error("NoSuchBucket: otterworks-files-prod".into()),
            ServiceError::DynamoError("connecting to dynamodb.internal:8000 refused".into()),
            ServiceError::SnsError("arn:aws:sns:us-east-1:123456789012:files".into()),
            ServiceError::Internal("thread panicked at src/handlers.rs:137".into()),
        ] {
            let body = body_of(&err).await;
            let message = body["message"].as_str().unwrap().to_string();
            assert!(
                !message.contains("otterworks-files-prod")
                    && !message.contains("dynamodb.internal")
                    && !message.contains("123456789012")
                    && !message.contains("src/handlers.rs"),
                "5xx body leaked backend detail: {message}"
            );
        }
    }
}

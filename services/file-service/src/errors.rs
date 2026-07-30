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
    use super::{ErrorResponse, ServiceError};
    use actix_web::{body::to_bytes, http::StatusCode, ResponseError};

    #[actix_web::test]
    async fn error_responses_have_expected_status_and_code() {
        let cases = vec![
            (
                ServiceError::FileNotFound("file".into()),
                StatusCode::NOT_FOUND,
                "file_not_found",
            ),
            (
                ServiceError::FolderNotFound("folder".into()),
                StatusCode::NOT_FOUND,
                "folder_not_found",
            ),
            (
                ServiceError::VersionNotFound("version".into()),
                StatusCode::NOT_FOUND,
                "version_not_found",
            ),
            (
                ServiceError::ShareNotFound("share".into()),
                StatusCode::NOT_FOUND,
                "share_not_found",
            ),
            (
                ServiceError::BadRequest("bad input".into()),
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
                ServiceError::Forbidden("no access".into()),
                StatusCode::FORBIDDEN,
                "forbidden",
            ),
            (
                ServiceError::S3Error("storage".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "storage_error",
            ),
            (
                ServiceError::DynamoError("metadata".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "metadata_error",
            ),
            (
                ServiceError::SnsError("event".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "event_error",
            ),
            (
                ServiceError::Internal("internal".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "internal_error",
            ),
        ];

        for (error, expected_status, expected_code) in cases {
            let response = error.error_response();
            assert_eq!(response.status(), expected_status);
            let body: serde_json::Value = serde_json::from_slice(
                &to_bytes(response.into_body()).await.expect("response body"),
            )
            .expect("JSON error response");
            assert_eq!(body["error"], expected_code);
        }
    }

    #[test]
    fn service_error_display_messages_match_thiserror_annotations() {
        assert_eq!(
            ServiceError::FileNotFound("file".into()).to_string(),
            "File not found: file"
        );
        assert_eq!(
            ServiceError::FolderNotFound("folder".into()).to_string(),
            "Folder not found: folder"
        );
        assert_eq!(
            ServiceError::VersionNotFound("version".into()).to_string(),
            "Version not found: version"
        );
        assert_eq!(
            ServiceError::ShareNotFound("share".into()).to_string(),
            "Share not found: share"
        );
        assert_eq!(
            ServiceError::BadRequest("bad input".into()).to_string(),
            "Bad request: bad input"
        );
        assert_eq!(
            ServiceError::FileTooLarge {
                max_bytes: 100,
                actual_bytes: 101
            }
            .to_string(),
            "File too large: max 100 bytes, got 101 bytes"
        );
        assert_eq!(
            ServiceError::Unauthorized("no token".into()).to_string(),
            "Unauthorized: no token"
        );
        assert_eq!(
            ServiceError::Forbidden("no access".into()).to_string(),
            "Forbidden: no access"
        );
        assert_eq!(
            ServiceError::S3Error("storage".into()).to_string(),
            "S3 error: storage"
        );
        assert_eq!(
            ServiceError::DynamoError("metadata".into()).to_string(),
            "DynamoDB error: metadata"
        );
        assert_eq!(
            ServiceError::SnsError("event".into()).to_string(),
            "SNS error: event"
        );
        assert_eq!(
            ServiceError::Internal("internal".into()).to_string(),
            "Internal error: internal"
        );
    }

    #[test]
    fn error_response_display_formats_code_and_message() {
        let response = ErrorResponse {
            error: "bad_request".into(),
            message: "Bad request: invalid name".into(),
        };

        assert_eq!(
            response.to_string(),
            "bad_request: Bad request: invalid name"
        );
    }
}

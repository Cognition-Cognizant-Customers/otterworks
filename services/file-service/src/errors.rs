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
    use actix_web::http::StatusCode;

    fn cases() -> Vec<(ServiceError, StatusCode, &'static str)> {
        vec![
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
                ServiceError::VersionNotFound("v".into()),
                StatusCode::NOT_FOUND,
                "version_not_found",
            ),
            (
                ServiceError::ShareNotFound("s".into()),
                StatusCode::NOT_FOUND,
                "share_not_found",
            ),
            (
                ServiceError::BadRequest("b".into()),
                StatusCode::BAD_REQUEST,
                "bad_request",
            ),
            (
                ServiceError::FileTooLarge {
                    max_bytes: 10,
                    actual_bytes: 11,
                },
                StatusCode::PAYLOAD_TOO_LARGE,
                "file_too_large",
            ),
            (
                ServiceError::Unauthorized("u".into()),
                StatusCode::UNAUTHORIZED,
                "unauthorized",
            ),
            (
                ServiceError::Forbidden("f".into()),
                StatusCode::FORBIDDEN,
                "forbidden",
            ),
            (
                ServiceError::S3Error("boom".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "storage_error",
            ),
            (
                ServiceError::DynamoError("boom".into()),
                StatusCode::INTERNAL_SERVER_ERROR,
                "metadata_error",
            ),
            (
                ServiceError::SnsError("boom".into()),
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

    #[test]
    fn every_variant_maps_to_its_status_and_error_type() {
        for (error, status, error_type) in cases() {
            let response = error.error_response();
            assert_eq!(response.status(), status, "status for {error_type}");
            assert_eq!(
                response.headers().get("content-type").unwrap(),
                "application/json",
                "content-type for {error_type}"
            );
        }
    }

    #[actix_rt::test]
    async fn the_body_carries_the_error_type_and_message() {
        let error = ServiceError::FileTooLarge {
            max_bytes: 10,
            actual_bytes: 11,
        };
        let expected_message = error.to_string();

        let body = actix_web::body::to_bytes(error.error_response().into_body())
            .await
            .unwrap();
        let json: serde_json::Value = serde_json::from_slice(&body).unwrap();

        assert_eq!(json["error"], "file_too_large");
        assert_eq!(json["message"], expected_message);
        assert_eq!(
            expected_message,
            "File too large: max 10 bytes, got 11 bytes"
        );
    }

    #[test]
    fn error_response_displays_as_type_and_message() {
        let response = ErrorResponse {
            error: "bad_request".into(),
            message: "owner_id is required".into(),
        };
        assert_eq!(response.to_string(), "bad_request: owner_id is required");
    }
}

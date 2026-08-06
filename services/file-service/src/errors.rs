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

    /// The wire contract of an error: HTTP status, `error` tag and `message`.
    async fn rendered(error: ServiceError) -> (StatusCode, String, String) {
        let response = error.error_response();
        let status = response.status();
        let body = to_bytes(response.into_body()).await.expect("error body");
        let json: serde_json::Value = serde_json::from_slice(&body).expect("error body is JSON");
        (
            status,
            json["error"].as_str().expect("error field").to_string(),
            json["message"].as_str().expect("message field").to_string(),
        )
    }

    #[actix_rt::test]
    async fn test_fileservice_file_not_found_maps_to_404() {
        let (status, error, message) = rendered(ServiceError::FileNotFound("abc".into())).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(error, "file_not_found");
        assert!(message.contains("abc"), "message was {message}");
    }

    #[actix_rt::test]
    async fn test_fileservice_folder_not_found_maps_to_404() {
        let (status, error, _) = rendered(ServiceError::FolderNotFound("abc".into())).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(error, "folder_not_found");
    }

    #[actix_rt::test]
    async fn test_fileservice_version_not_found_maps_to_404() {
        let (status, error, _) = rendered(ServiceError::VersionNotFound("abc".into())).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(error, "version_not_found");
    }

    #[actix_rt::test]
    async fn test_fileservice_share_not_found_maps_to_404() {
        let (status, error, _) = rendered(ServiceError::ShareNotFound("abc".into())).await;
        assert_eq!(status, StatusCode::NOT_FOUND);
        assert_eq!(error, "share_not_found");
    }

    #[actix_rt::test]
    async fn test_fileservice_bad_request_maps_to_400() {
        let (status, error, message) =
            rendered(ServiceError::BadRequest("invalid owner_id".into())).await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert_eq!(error, "bad_request");
        assert!(
            message.contains("invalid owner_id"),
            "message was {message}"
        );
    }

    #[actix_rt::test]
    async fn test_fileservice_file_too_large_maps_to_413() {
        let (status, error, message) = rendered(ServiceError::FileTooLarge {
            max_bytes: 10,
            actual_bytes: 11,
        })
        .await;
        assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
        assert_eq!(error, "file_too_large");
        assert!(message.contains("10") && message.contains("11"));
    }

    #[actix_rt::test]
    async fn test_fileservice_unauthorized_maps_to_401() {
        let (status, error, _) = rendered(ServiceError::Unauthorized("no token".into())).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED);
        assert_eq!(error, "unauthorized");
    }

    #[actix_rt::test]
    async fn test_fileservice_forbidden_maps_to_403() {
        let (status, error, _) = rendered(ServiceError::Forbidden("not yours".into())).await;
        assert_eq!(status, StatusCode::FORBIDDEN);
        assert_eq!(error, "forbidden");
    }

    #[actix_rt::test]
    async fn test_fileservice_s3_error_maps_to_500_storage_error() {
        let (status, error, _) = rendered(ServiceError::S3Error("no bucket".into())).await;
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(error, "storage_error");
    }

    #[actix_rt::test]
    async fn test_fileservice_dynamo_error_maps_to_500_metadata_error() {
        let (status, error, _) = rendered(ServiceError::DynamoError("throttled".into())).await;
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(error, "metadata_error");
    }

    #[actix_rt::test]
    async fn test_fileservice_sns_error_maps_to_500_event_error() {
        let (status, error, _) = rendered(ServiceError::SnsError("no topic".into())).await;
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(error, "event_error");
    }

    #[actix_rt::test]
    async fn test_fileservice_internal_error_maps_to_500_internal_error() {
        let (status, error, _) = rendered(ServiceError::Internal("boom".into())).await;
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(error, "internal_error");
    }

    #[actix_rt::test]
    async fn test_fileservice_error_response_is_json_with_error_and_message() {
        let response = ServiceError::BadRequest("bad".into()).error_response();
        let content_type = response
            .headers()
            .get("content-type")
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default()
            .to_string();
        assert!(
            content_type.starts_with("application/json"),
            "content-type was {content_type}"
        );

        let body = to_bytes(response.into_body()).await.expect("error body");
        let json: serde_json::Value = serde_json::from_slice(&body).expect("error body is JSON");
        assert_eq!(json["error"], "bad_request");
        assert_eq!(json["message"], "Bad request: bad");
    }
}

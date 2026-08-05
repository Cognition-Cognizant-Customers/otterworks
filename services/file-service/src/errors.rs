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

    async fn body_json(resp: HttpResponse) -> serde_json::Value {
        let bytes = actix_web::body::to_bytes(resp.into_body()).await.unwrap();
        serde_json::from_slice(&bytes).unwrap()
    }

    #[actix_rt::test]
    async fn test_not_found_variants_map_to_404() {
        for (err, error_type) in [
            (ServiceError::FileNotFound("f1".into()), "file_not_found"),
            (
                ServiceError::FolderNotFound("d1".into()),
                "folder_not_found",
            ),
            (
                ServiceError::VersionNotFound("v1".into()),
                "version_not_found",
            ),
            (ServiceError::ShareNotFound("s1".into()), "share_not_found"),
        ] {
            let resp = err.error_response();
            assert_eq!(resp.status(), StatusCode::NOT_FOUND);
            let json = body_json(resp).await;
            assert_eq!(json["error"], error_type);
        }
    }

    #[actix_rt::test]
    async fn test_bad_request_maps_to_400_with_message() {
        let resp = ServiceError::BadRequest("owner_id is required".into()).error_response();
        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        let json = body_json(resp).await;
        assert_eq!(json["error"], "bad_request");
        assert_eq!(json["message"], "Bad request: owner_id is required");
    }

    #[actix_rt::test]
    async fn test_file_too_large_maps_to_413_with_sizes() {
        let resp = ServiceError::FileTooLarge {
            max_bytes: 100,
            actual_bytes: 250,
        }
        .error_response();
        assert_eq!(resp.status(), StatusCode::PAYLOAD_TOO_LARGE);
        let json = body_json(resp).await;
        assert_eq!(json["error"], "file_too_large");
        assert_eq!(
            json["message"],
            "File too large: max 100 bytes, got 250 bytes"
        );
    }

    #[actix_rt::test]
    async fn test_auth_variants_map_to_401_and_403() {
        let resp = ServiceError::Unauthorized("no token".into()).error_response();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(body_json(resp).await["error"], "unauthorized");

        let resp = ServiceError::Forbidden("not owner".into()).error_response();
        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
        assert_eq!(body_json(resp).await["error"], "forbidden");
    }

    #[actix_rt::test]
    async fn test_backend_errors_map_to_500_with_distinct_types() {
        for (err, error_type) in [
            (ServiceError::S3Error("s3 down".into()), "storage_error"),
            (
                ServiceError::DynamoError("ddb down".into()),
                "metadata_error",
            ),
            (ServiceError::SnsError("sns down".into()), "event_error"),
            (ServiceError::Internal("boom".into()), "internal_error"),
        ] {
            let resp = err.error_response();
            assert_eq!(resp.status(), StatusCode::INTERNAL_SERVER_ERROR);
            assert_eq!(body_json(resp).await["error"], error_type);
        }
    }
}

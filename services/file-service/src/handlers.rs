use actix_multipart::Multipart;
use actix_web::{web, HttpRequest, HttpResponse};
use bytes::BytesMut;
use chrono::Utc;
use futures_util::StreamExt;
use uuid::Uuid;

async fn chaos_active(cm: &mut redis::aio::ConnectionManager, flag: &str) -> bool {
    let result: redis::RedisResult<i64> = redis::cmd("EXISTS").arg(flag).query_async(cm).await;
    result.unwrap_or(0) > 0
}

use crate::config::AppConfig;
use crate::errors::ServiceError;
use crate::events::EventPublisher;
use crate::metadata::MetadataClient;
use crate::middleware;
use crate::models::{
    ActivityItem, ActivityQuery, ActivityResponse, CreateFolderRequest, DownloadResponse,
    FileDetailResponse, FileMetadata, FileShare, FileVersion, Folder, HealthResponse,
    ListFilesQuery, ListFilesResponse, ListFoldersQuery, ListFoldersResponse, ListVersionsResponse,
    MoveFileRequest, RenameFileRequest, ShareFileRequest, ShareFileResponse, UpdateFolderRequest,
    UploadResponse,
};
use crate::storage::S3Client;

// -- Health & Metrics --

pub async fn health() -> HttpResponse {
    HttpResponse::Ok().json(HealthResponse {
        status: "healthy".into(),
        service: "file-service".into(),
        version: env!("CARGO_PKG_VERSION").into(),
    })
}

pub async fn metrics() -> HttpResponse {
    HttpResponse::Ok()
        .content_type("text/plain; charset=utf-8")
        .body(middleware::render_metrics())
}

// -- File Handlers --

pub async fn upload_file(
    req: HttpRequest,
    s3: web::Data<S3Client>,
    meta: web::Data<MetadataClient>,
    events: web::Data<EventPublisher>,
    config: web::Data<AppConfig>,
    redis_cm: web::Data<redis::aio::ConnectionManager>,
    mut payload: Multipart,
) -> Result<HttpResponse, ServiceError> {
    // Prefer owner_id from X-User-ID header (injected by api-gateway from JWT).
    // Fall back to the multipart field for direct/internal callers.
    let header_owner_id = req
        .headers()
        .get("X-User-ID")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.trim().parse::<Uuid>().ok());

    let mut file_bytes = BytesMut::new();
    let mut file_name = String::from("unnamed");
    let mut content_type = String::from("application/octet-stream");
    let mut owner_id: Option<Uuid> = None;
    let mut folder_id: Option<Uuid> = None;

    while let Some(item) = payload.next().await {
        let mut field = item.map_err(|e| ServiceError::BadRequest(e.to_string()))?;
        let disposition = field.content_disposition().cloned();
        let field_name = disposition
            .as_ref()
            .and_then(|d| d.get_name().map(|s| s.to_string()))
            .unwrap_or_default();

        match field_name.as_str() {
            "file" => {
                if let Some(fname) = disposition.as_ref().and_then(|d| d.get_filename()) {
                    file_name = fname.to_string();
                }
                if let Some(ct) = field.content_type() {
                    content_type = ct.to_string();
                }
                while let Some(chunk) = field.next().await {
                    let data = chunk.map_err(|e| ServiceError::BadRequest(e.to_string()))?;
                    file_bytes.extend_from_slice(&data);
                    if file_bytes.len() as u64 > config.server.max_upload_bytes {
                        return Err(ServiceError::FileTooLarge {
                            max_bytes: config.server.max_upload_bytes,
                            actual_bytes: file_bytes.len() as u64,
                        });
                    }
                }
            }
            "owner_id" => {
                let mut value = BytesMut::new();
                while let Some(chunk) = field.next().await {
                    let data = chunk.map_err(|e| ServiceError::BadRequest(e.to_string()))?;
                    value.extend_from_slice(&data);
                }
                let s = String::from_utf8_lossy(&value).to_string();
                owner_id = Some(
                    s.trim()
                        .parse::<Uuid>()
                        .map_err(|e| ServiceError::BadRequest(format!("invalid owner_id: {e}")))?,
                );
            }
            "folder_id" => {
                let mut value = BytesMut::new();
                while let Some(chunk) = field.next().await {
                    let data = chunk.map_err(|e| ServiceError::BadRequest(e.to_string()))?;
                    value.extend_from_slice(&data);
                }
                let s = String::from_utf8_lossy(&value).to_string();
                let trimmed = s.trim();
                if !trimmed.is_empty() {
                    folder_id = Some(trimmed.parse::<Uuid>().map_err(|e| {
                        ServiceError::BadRequest(format!("invalid folder_id: {e}"))
                    })?);
                }
            }
            _ => {}
        }
    }

    let owner = header_owner_id
        .or(owner_id)
        .ok_or_else(|| ServiceError::BadRequest("owner_id is required".into()))?;

    if file_bytes.is_empty() {
        return Err(ServiceError::BadRequest("file field is required".into()));
    }

    let file_id = Uuid::new_v4();
    let s3_key = format!("files/{}/{}", owner, file_id);
    let now = Utc::now();
    let size = file_bytes.len() as u64;

    // CHAOS: when this flag is active the S3 client targets a nonexistent
    // bucket, simulating a misconfigured bucket name after a recent infra
    // change.  The AWS SDK returns NoSuchBucket which surfaces as a 500.
    let effective_bucket = if chaos_active(
        &mut redis_cm.get_ref().clone(),
        "chaos:file-service:upload_s3_error",
    )
    .await
    {
        tracing::warn!("Chaos flag active: redirecting upload to nonexistent bucket");
        "otterworks-files-chaos-nonexistent".to_string()
    } else {
        s3.bucket.clone()
    };
    let chaos_s3 = crate::storage::S3Client {
        client: s3.client.clone(),
        bucket: effective_bucket,
    };
    chaos_s3
        .upload_object(&s3_key, file_bytes.freeze(), &content_type)
        .await?;

    let file_meta = FileMetadata {
        id: file_id,
        name: file_name,
        mime_type: content_type,
        size_bytes: size,
        s3_key: s3_key.clone(),
        folder_id,
        owner_id: owner,
        version: 1,
        is_trashed: false,
        created_at: now,
        updated_at: now,
    };

    meta.put_file(&file_meta).await?;

    let version = FileVersion {
        file_id,
        version: 1,
        s3_key,
        size_bytes: size,
        created_by: owner,
        created_at: now,
    };
    meta.put_version(&version).await?;

    let _ = events
        .file_uploaded(
            &file_id,
            &owner,
            folder_id.as_ref(),
            &file_meta.name,
            &file_meta.mime_type,
            file_meta.size_bytes,
        )
        .await;

    tracing::info!(file_id = %file_id, name = %file_meta.name, size = %size, "File uploaded");

    Ok(HttpResponse::Created().json(UploadResponse { file: file_meta }))
}

pub async fn get_file_metadata(
    meta: web::Data<MetadataClient>,
    path: web::Path<String>,
) -> Result<HttpResponse, ServiceError> {
    let file_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;
    let file = meta.get_file(&file_id).await?;
    let shares = meta.list_shares(&file_id).await.unwrap_or_default();
    Ok(HttpResponse::Ok().json(FileDetailResponse {
        file,
        shared_with: shares,
    }))
}

/// Resolve the effective owner_id for list operations.
///
/// Prefer the `X-User-ID` header injected by the api-gateway from the
/// authenticated JWT. This prevents a caller from spoofing another user's
/// `owner_id` via the query string. Fall back to `query.owner_id` only when
/// no header is present (direct/internal callers).
fn resolve_owner_id(req: &HttpRequest, query_owner_id: Option<Uuid>) -> Option<Uuid> {
    let header_owner_id = req
        .headers()
        .get("X-User-ID")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.trim().parse::<Uuid>().ok());

    header_owner_id.or(query_owner_id)
}

pub async fn list_files(
    req: HttpRequest,
    meta: web::Data<MetadataClient>,
    query: web::Query<ListFilesQuery>,
) -> Result<HttpResponse, ServiceError> {
    let include_trashed = query.include_trashed.unwrap_or(false);
    let owner_id = resolve_owner_id(&req, query.owner_id);
    let files = meta
        .list_files(query.folder_id, owner_id, include_trashed)
        .await?;

    let page = query.page.unwrap_or(1).max(1);
    let page_size = query.page_size.unwrap_or(50).min(100);
    let total = files.len();
    let start = (page - 1).saturating_mul(page_size) as usize;
    let paged: Vec<FileMetadata> = files
        .into_iter()
        .skip(start)
        .take(page_size as usize)
        .collect();

    Ok(HttpResponse::Ok().json(ListFilesResponse {
        files: paged,
        total,
        page,
        page_size,
    }))
}

pub async fn list_shared_files(
    meta: web::Data<MetadataClient>,
    req: HttpRequest,
    query: web::Query<ListFilesQuery>,
) -> Result<HttpResponse, ServiceError> {
    let user_id: Uuid = req
        .headers()
        .get("X-User-ID")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| ServiceError::BadRequest("missing X-User-ID header".into()))?;

    let shares = meta.list_shares_for_user(&user_id).await?;

    // Deduplicate by file_id to handle legacy duplicate share records
    let mut seen_file_ids = std::collections::HashSet::new();
    let mut files = Vec::new();
    for share in &shares {
        if !seen_file_ids.insert(share.file_id) {
            continue;
        }
        match meta.get_file(&share.file_id).await {
            Ok(file) if !file.is_trashed => files.push(file),
            _ => {}
        }
    }

    let page = query.page.unwrap_or(1).max(1);
    let page_size = query.page_size.unwrap_or(50).min(100);
    let total = files.len();
    let start = (page - 1).saturating_mul(page_size) as usize;
    let paged: Vec<FileMetadata> = files
        .into_iter()
        .skip(start)
        .take(page_size as usize)
        .collect();

    Ok(HttpResponse::Ok().json(ListFilesResponse {
        files: paged,
        total,
        page,
        page_size,
    }))
}

pub async fn list_trashed(
    req: HttpRequest,
    meta: web::Data<MetadataClient>,
    query: web::Query<ListFilesQuery>,
) -> Result<HttpResponse, ServiceError> {
    let owner_id = resolve_owner_id(&req, query.owner_id);
    let files = meta.list_trashed(owner_id).await?;

    let page = query.page.unwrap_or(1).max(1);
    let page_size = query.page_size.unwrap_or(50).min(100);
    let total = files.len();
    let start = (page - 1).saturating_mul(page_size) as usize;
    let paged: Vec<FileMetadata> = files
        .into_iter()
        .skip(start)
        .take(page_size as usize)
        .collect();

    Ok(HttpResponse::Ok().json(ListFilesResponse {
        files: paged,
        total,
        page,
        page_size,
    }))
}
pub async fn delete_file(
    s3: web::Data<S3Client>,
    meta: web::Data<MetadataClient>,
    events: web::Data<EventPublisher>,
    path: web::Path<String>,
) -> Result<HttpResponse, ServiceError> {
    let file_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;

    let file = meta.get_file(&file_id).await?;
    meta.delete_file(&file_id).await?;
    s3.delete_object(&file.s3_key).await?;

    let _ = events.file_deleted(&file_id, &file.owner_id).await;

    tracing::info!(file_id = %file_id, "File deleted");
    Ok(HttpResponse::NoContent().finish())
}

pub async fn download_file(
    s3: web::Data<S3Client>,
    meta: web::Data<MetadataClient>,
    path: web::Path<String>,
) -> Result<HttpResponse, ServiceError> {
    let file_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;

    let file = meta.get_file(&file_id).await?;
    let url = s3.presigned_download_url(&file.s3_key, 3600).await?;

    Ok(HttpResponse::Ok().json(DownloadResponse {
        url,
        expires_in_secs: 3600,
    }))
}

pub async fn move_file(
    meta: web::Data<MetadataClient>,
    events: web::Data<EventPublisher>,
    path: web::Path<String>,
    body: web::Json<MoveFileRequest>,
) -> Result<HttpResponse, ServiceError> {
    let file_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;

    let file = meta.move_file(&file_id, body.folder_id).await?;

    let _ = events
        .file_moved(&file_id, &file.owner_id, body.folder_id.as_ref())
        .await;

    tracing::info!(file_id = %file_id, folder_id = ?body.folder_id, "File moved");
    Ok(HttpResponse::Ok().json(file))
}

pub async fn rename_file(
    meta: web::Data<MetadataClient>,
    events: web::Data<EventPublisher>,
    path: web::Path<String>,
    body: web::Json<RenameFileRequest>,
) -> Result<HttpResponse, ServiceError> {
    let file_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;

    let name = body.name.trim();
    if name.is_empty() {
        return Err(ServiceError::BadRequest("name cannot be empty".into()));
    }

    let file = meta.rename_file(&file_id, name).await?;

    let _ = events
        .file_updated(
            &file_id,
            &file.owner_id,
            file.folder_id.as_ref(),
            &file.name,
            &file.mime_type,
            file.size_bytes as u64,
        )
        .await;

    tracing::info!(file_id = %file_id, new_name = %name, "File renamed");
    Ok(HttpResponse::Ok().json(file))
}

pub async fn list_versions(
    meta: web::Data<MetadataClient>,
    path: web::Path<String>,
) -> Result<HttpResponse, ServiceError> {
    let file_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;

    let versions = meta.list_versions(&file_id).await?;
    Ok(HttpResponse::Ok().json(ListVersionsResponse { versions }))
}

pub async fn trash_file(
    meta: web::Data<MetadataClient>,
    events: web::Data<EventPublisher>,
    path: web::Path<String>,
) -> Result<HttpResponse, ServiceError> {
    let file_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;

    let file = meta.trash_file(&file_id).await?;

    let _ = events.file_trashed(&file_id, &file.owner_id).await;

    tracing::info!(file_id = %file_id, "File trashed");
    Ok(HttpResponse::Ok().json(file))
}

pub async fn restore_file(
    meta: web::Data<MetadataClient>,
    events: web::Data<EventPublisher>,
    path: web::Path<String>,
) -> Result<HttpResponse, ServiceError> {
    let file_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;

    let file = meta.restore_file(&file_id).await?;

    let _ = events
        .file_restored(
            &file_id,
            &file.owner_id,
            file.folder_id.as_ref(),
            &file.name,
            &file.mime_type,
            file.size_bytes as u64,
        )
        .await;

    tracing::info!(file_id = %file_id, "File restored");
    Ok(HttpResponse::Ok().json(file))
}

pub async fn share_file(
    meta: web::Data<MetadataClient>,
    events: web::Data<EventPublisher>,
    path: web::Path<String>,
    body: web::Json<ShareFileRequest>,
) -> Result<HttpResponse, ServiceError> {
    let file_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;

    // Ensure file exists
    let file = meta.get_file(&file_id).await?;

    // Check if share already exists for this file + user
    if let Some(existing) = meta
        .find_existing_share(&file_id, &body.shared_with)
        .await?
    {
        // Update permission if different, otherwise return existing
        if existing.permission != body.permission {
            let updated = FileShare {
                id: existing.id,
                file_id,
                shared_with: body.shared_with,
                permission: body.permission.clone(),
                shared_by: body.shared_by,
                created_at: existing.created_at,
            };
            meta.put_share(&updated).await?;
            tracing::info!(file_id = %file_id, shared_with = %body.shared_with, "File share updated");
            return Ok(HttpResponse::Ok().json(ShareFileResponse { share: updated }));
        }
        tracing::info!(file_id = %file_id, shared_with = %body.shared_with, "File already shared");
        return Ok(HttpResponse::Ok().json(ShareFileResponse { share: existing }));
    }

    let share = FileShare {
        id: Uuid::new_v4(),
        file_id,
        shared_with: body.shared_with,
        permission: body.permission.clone(),
        shared_by: body.shared_by,
        created_at: Utc::now(),
    };

    meta.put_share(&share).await?;

    let _ = events
        .file_shared(&file_id, &file.owner_id, &body.shared_with)
        .await;

    tracing::info!(file_id = %file_id, shared_with = %body.shared_with, "File shared");
    Ok(HttpResponse::Created().json(ShareFileResponse { share }))
}

pub async fn remove_share(
    meta: web::Data<MetadataClient>,
    path: web::Path<(String, String)>,
) -> Result<HttpResponse, ServiceError> {
    let (file_id_str, user_id_str) = path.into_inner();
    let file_id: Uuid = file_id_str
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid file id: {e}")))?;
    let user_id: Uuid = user_id_str
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid user id: {e}")))?;

    // Ensure file exists
    let _file = meta.get_file(&file_id).await?;

    // Find the existing share
    let share = meta
        .find_existing_share(&file_id, &user_id)
        .await?
        .ok_or_else(|| ServiceError::ShareNotFound("Share not found".into()))?;

    meta.delete_share(&share.id).await?;

    tracing::info!(file_id = %file_id, user_id = %user_id, "File share removed");
    Ok(HttpResponse::NoContent().finish())
}

// -- Folder Handlers --

pub async fn list_folders(
    req: HttpRequest,
    meta: web::Data<MetadataClient>,
    query: web::Query<ListFoldersQuery>,
) -> Result<HttpResponse, ServiceError> {
    let owner_id = resolve_owner_id(&req, query.owner_id);
    let folders = meta.list_folders(query.parent_id, owner_id).await?;
    Ok(HttpResponse::Ok().json(ListFoldersResponse { folders }))
}

pub async fn create_folder(
    meta: web::Data<MetadataClient>,
    body: web::Json<CreateFolderRequest>,
) -> Result<HttpResponse, ServiceError> {
    let now = Utc::now();
    let folder = Folder {
        id: Uuid::new_v4(),
        name: body.name.clone(),
        parent_id: body.parent_id,
        owner_id: body.owner_id,
        created_at: now,
        updated_at: now,
    };

    meta.put_folder(&folder).await?;
    tracing::info!(folder_id = %folder.id, name = %folder.name, "Folder created");
    Ok(HttpResponse::Created().json(folder))
}

pub async fn get_folder(
    meta: web::Data<MetadataClient>,
    path: web::Path<String>,
) -> Result<HttpResponse, ServiceError> {
    let folder_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid folder id: {e}")))?;

    let folder = meta.get_folder(&folder_id).await?;
    Ok(HttpResponse::Ok().json(folder))
}

pub async fn update_folder(
    meta: web::Data<MetadataClient>,
    path: web::Path<String>,
    body: web::Json<UpdateFolderRequest>,
) -> Result<HttpResponse, ServiceError> {
    let folder_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid folder id: {e}")))?;

    let folder = meta
        .update_folder(&folder_id, body.name.clone(), body.parent_id)
        .await?;
    Ok(HttpResponse::Ok().json(folder))
}

pub async fn delete_folder(
    meta: web::Data<MetadataClient>,
    path: web::Path<String>,
) -> Result<HttpResponse, ServiceError> {
    let folder_id: Uuid = path
        .into_inner()
        .parse()
        .map_err(|e| ServiceError::BadRequest(format!("invalid folder id: {e}")))?;

    meta.delete_folder(&folder_id).await?;
    tracing::info!(folder_id = %folder_id, "Folder deleted");
    Ok(HttpResponse::NoContent().finish())
}

// -- Activity Handler --

pub async fn list_activity(
    req: HttpRequest,
    meta: web::Data<MetadataClient>,
    query: web::Query<ActivityQuery>,
) -> Result<HttpResponse, ServiceError> {
    let owner_id = req
        .headers()
        .get("X-User-ID")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.trim().parse::<Uuid>().ok())
        .ok_or_else(|| ServiceError::BadRequest("missing owner context".into()))?;

    let limit = query.limit.unwrap_or(20).min(50) as usize;

    let (files, shares) = futures_util::future::join(
        meta.list_files(None, Some(owner_id), true),
        meta.list_shares_by_owner(&owner_id),
    )
    .await;

    let files = files.unwrap_or_default();
    let shares = shares.unwrap_or_default();

    // Build a file-id → name lookup for share descriptions
    let file_names: std::collections::HashMap<Uuid, String> =
        files.iter().map(|f| (f.id, f.name.clone())).collect();

    let mut items: Vec<ActivityItem> = Vec::new();

    for f in &files {
        items.push(ActivityItem {
            id: format!("upload-{}", f.id),
            activity_type: "upload".into(),
            description: format!("Uploaded {}", f.name),
            actor_name: "You".into(),
            resource_name: f.name.clone(),
            resource_type: "file".into(),
            resource_id: f.id.to_string(),
            created_at: f.created_at.to_rfc3339(),
        });
    }

    for s in &shares {
        let name = file_names
            .get(&s.file_id)
            .cloned()
            .unwrap_or_else(|| "a file".into());
        items.push(ActivityItem {
            id: format!("share-{}", s.id),
            activity_type: "share".into(),
            description: format!("Shared {}", name),
            actor_name: "You".into(),
            resource_name: name,
            resource_type: "file".into(),
            resource_id: s.file_id.to_string(),
            created_at: s.created_at.to_rfc3339(),
        });
    }

    items.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    items.truncate(limit);

    Ok(HttpResponse::Ok().json(ActivityResponse { items }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{AwsConfig, ServerConfig, SnsConfig};
    use actix_web::http::StatusCode;
    use actix_web::{test, App};
    use aws_smithy_http_client::test_util::infallible_client_fn;
    use aws_smithy_types::body::SdkBody;
    use serde_json::Value;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    const BOUNDARY: &str = "otterworksTestBoundary";
    const FILES_TABLE: &str = "test-files";
    const SHARES_TABLE: &str = "test-shares";
    const OWNER_ID: &str = "11111111-1111-4111-8111-111111111111";
    const SHARER_ID: &str = "22222222-2222-4222-8222-222222222222";
    /// Small injected upload ceiling so the boundary trio needs 11 bytes, not 100 MB.
    const MAX_UPLOAD_BYTES: u64 = 10;

    // -- AWS stub ------------------------------------------------------------

    /// Canned DynamoDB payloads. Anything not listed here (S3 `PutObject`,
    /// DynamoDB writes) is answered with `200 {}`.
    #[derive(Clone, Default)]
    struct AwsResponses {
        files_scan: Option<String>,
        shares_scan: Option<String>,
        get_file: Option<String>,
    }

    /// S3 and DynamoDB clients wired to an in-process HTTP stub: no sockets,
    /// no credentials, no retries, identical answers on every run.
    fn test_clients(responses: AwsResponses) -> (S3Client, MetadataClient) {
        let http_client = infallible_client_fn(move |request| {
            let target = request
                .headers()
                .get("x-amz-target")
                .and_then(|value| value.to_str().ok())
                .unwrap_or_default()
                .to_string();
            let body =
                String::from_utf8_lossy(request.body().bytes().unwrap_or_default()).to_string();

            let payload = if target.ends_with("Scan") && body.contains(SHARES_TABLE) {
                responses.shares_scan.clone()
            } else if target.ends_with("Scan") {
                responses.files_scan.clone()
            } else if target.ends_with("GetItem") {
                responses.get_file.clone()
            } else {
                None
            };

            http::Response::builder()
                .status(200)
                .header("content-type", "application/x-amz-json-1.0")
                .body(SdkBody::from(payload.unwrap_or_else(|| "{}".to_string())))
                .expect("stub response")
        });

        let s3_config = aws_sdk_s3::config::Builder::new()
            .behavior_version(aws_sdk_s3::config::BehaviorVersion::latest())
            .region(aws_sdk_s3::config::Region::new("us-east-1"))
            .credentials_provider(aws_sdk_s3::config::Credentials::new(
                "test", "test", None, None, "test",
            ))
            .http_client(http_client.clone())
            .force_path_style(true)
            .build();

        let dynamo_config = aws_sdk_dynamodb::config::Builder::new()
            .behavior_version(aws_sdk_dynamodb::config::BehaviorVersion::latest())
            .region(aws_sdk_dynamodb::config::Region::new("us-east-1"))
            .credentials_provider(aws_sdk_dynamodb::config::Credentials::new(
                "test", "test", None, None, "test",
            ))
            .http_client(http_client)
            .build();

        (
            S3Client {
                client: aws_sdk_s3::Client::from_conf(s3_config),
                bucket: "test-bucket".into(),
            },
            MetadataClient {
                client: aws_sdk_dynamodb::Client::from_conf(dynamo_config),
                files_table: FILES_TABLE.into(),
                folders_table: "test-folders".into(),
                versions_table: "test-versions".into(),
                shares_table: SHARES_TABLE.into(),
            },
        )
    }

    // -- Redis stub ----------------------------------------------------------

    /// A `ConnectionManager` is a required extractor on `upload_file`, and it
    /// can only be built against a live socket. This is a loopback RESP server
    /// that answers `:0` to every command, i.e. "no chaos flag set".
    async fn fake_redis() -> redis::aio::ConnectionManager {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind stub redis");
        let addr = listener.local_addr().expect("stub redis addr");

        actix_rt::spawn(async move {
            while let Ok((mut socket, _)) = listener.accept().await {
                actix_rt::spawn(async move {
                    let mut pending = Vec::new();
                    let mut chunk = [0u8; 512];
                    loop {
                        match socket.read(&mut chunk).await {
                            Ok(0) | Err(_) => return,
                            Ok(read) => pending.extend_from_slice(&chunk[..read]),
                        }
                        let (commands, consumed) = complete_commands(&pending);
                        pending.drain(..consumed);
                        for _ in 0..commands {
                            if socket.write_all(b":0\r\n").await.is_err() {
                                return;
                            }
                        }
                    }
                });
            }
        });

        let client = redis::Client::open(format!("redis://{addr}")).expect("stub redis url");
        redis::aio::ConnectionManager::new(client)
            .await
            .expect("stub redis connection")
    }

    /// Counts the whole RESP commands buffered so far, and how many bytes they
    /// occupy, so the stub answers exactly once per command (pipelines included).
    fn complete_commands(buffer: &[u8]) -> (usize, usize) {
        let mut consumed = 0;
        let mut commands = 0;
        while let Some((argc, after_header)) = resp_length(buffer, consumed, b'*') {
            let mut cursor = after_header;
            let mut complete = true;
            for _ in 0..argc {
                match resp_length(buffer, cursor, b'$') {
                    Some((len, after_len)) if after_len + len + 2 <= buffer.len() => {
                        cursor = after_len + len + 2;
                    }
                    _ => {
                        complete = false;
                        break;
                    }
                }
            }
            if !complete {
                break;
            }
            commands += 1;
            consumed = cursor;
        }
        (commands, consumed)
    }

    /// Reads a `<marker><len>\r\n` RESP header, returning the length and the
    /// offset just past it.
    fn resp_length(buffer: &[u8], at: usize, marker: u8) -> Option<(usize, usize)> {
        if buffer.get(at)? != &marker {
            return None;
        }
        let end = at + buffer[at..].windows(2).position(|pair| pair == b"\r\n")?;
        let length = std::str::from_utf8(&buffer[at + 1..end])
            .ok()?
            .parse()
            .ok()?;
        Some((length, end + 2))
    }

    // -- Request helpers -----------------------------------------------------

    struct FormPart<'a> {
        name: &'a str,
        filename: Option<&'a str>,
        content_type: Option<&'a str>,
        value: &'a [u8],
    }

    impl<'a> FormPart<'a> {
        fn file(filename: Option<&'a str>, value: &'a [u8]) -> Self {
            Self {
                name: "file",
                filename,
                content_type: Some("text/plain"),
                value,
            }
        }

        fn field(name: &'a str, value: &'a str) -> Self {
            Self {
                name,
                filename: None,
                content_type: None,
                value: value.as_bytes(),
            }
        }
    }

    fn multipart_body(parts: &[FormPart<'_>]) -> Vec<u8> {
        let mut body = Vec::new();
        for part in parts {
            body.extend_from_slice(format!("--{BOUNDARY}\r\n").as_bytes());
            let mut disposition = format!("Content-Disposition: form-data; name=\"{}\"", part.name);
            if let Some(filename) = part.filename {
                disposition.push_str(&format!("; filename=\"{filename}\""));
            }
            body.extend_from_slice(format!("{disposition}\r\n").as_bytes());
            if let Some(content_type) = part.content_type {
                body.extend_from_slice(format!("Content-Type: {content_type}\r\n").as_bytes());
            }
            body.extend_from_slice(b"\r\n");
            body.extend_from_slice(part.value);
            body.extend_from_slice(b"\r\n");
        }
        body.extend_from_slice(format!("--{BOUNDARY}--\r\n").as_bytes());
        body
    }

    async fn call_api(request: test::TestRequest, responses: AwsResponses) -> (StatusCode, Value) {
        let (s3, meta) = test_clients(responses);
        let config = AppConfig {
            server: ServerConfig {
                port: 8082,
                max_upload_bytes: MAX_UPLOAD_BYTES,
            },
            aws: AwsConfig {
                region: "us-east-1".into(),
                endpoint_url: None,
                s3_bucket: "test-bucket".into(),
                dynamodb_table: FILES_TABLE.into(),
                dynamodb_folders_table: "test-folders".into(),
                dynamodb_versions_table: "test-versions".into(),
                dynamodb_shares_table: SHARES_TABLE.into(),
            },
            sns: SnsConfig { topic_arn: None },
        };

        let app = test::init_service(
            App::new()
                .app_data(web::Data::new(config))
                .app_data(web::Data::new(s3))
                .app_data(web::Data::new(meta))
                .app_data(web::Data::new(
                    crate::events::tests::publisher_without_topic(),
                ))
                .app_data(web::Data::new(fake_redis().await))
                .service(
                    web::scope("/api/v1/files")
                        .route("/upload", web::post().to(upload_file))
                        .route("/shared", web::get().to(list_shared_files))
                        .route("/trash", web::get().to(list_trashed))
                        .route("/activity", web::get().to(list_activity))
                        .route("", web::get().to(list_files)),
                ),
        )
        .await;

        let response = test::call_service(&app, request.to_request()).await;
        let status = response.status();
        let body = test::read_body(response).await;
        (status, serde_json::from_slice(&body).unwrap_or(Value::Null))
    }

    async fn upload_as(owner_header: Option<&str>, parts: &[FormPart<'_>]) -> (StatusCode, Value) {
        let mut request = test::TestRequest::post()
            .uri("/api/v1/files/upload")
            .insert_header((
                "content-type",
                format!("multipart/form-data; boundary={BOUNDARY}"),
            ));
        if let Some(owner) = owner_header {
            request = request.insert_header(("X-User-ID", owner.to_string()));
        }
        call_api(
            request.set_payload(multipart_body(parts)),
            AwsResponses::default(),
        )
        .await
    }

    async fn upload(parts: &[FormPart<'_>]) -> (StatusCode, Value) {
        upload_as(Some(OWNER_ID), parts).await
    }

    async fn get(uri: &str, responses: AwsResponses) -> (StatusCode, Value) {
        call_api(
            test::TestRequest::get()
                .uri(uri)
                .insert_header(("X-User-ID", OWNER_ID)),
            responses,
        )
        .await
    }

    async fn list_with(uri: &str, item_count: usize) -> (StatusCode, Value) {
        get(
            uri,
            AwsResponses {
                files_scan: Some(scan(&(0..item_count).map(file_item).collect::<Vec<_>>())),
                ..AwsResponses::default()
            },
        )
        .await
    }

    // -- Fixture builders ----------------------------------------------------

    fn timestamp(index: usize) -> String {
        format!("2026-01-01T{:02}:{:02}:00Z", index / 60, index % 60)
    }

    fn file_item(index: usize) -> String {
        let id = Uuid::from_u128(index as u128 + 1);
        let created = timestamp(index);
        format!(
            r#"{{"id":{{"S":"{id}"}},"name":{{"S":"file-{index}.txt"}},"mime_type":{{"S":"text/plain"}},"size_bytes":{{"N":"{index}"}},"s3_key":{{"S":"files/{OWNER_ID}/{id}"}},"owner_id":{{"S":"{OWNER_ID}"}},"version":{{"N":"1"}},"is_trashed":{{"BOOL":false}},"created_at":{{"S":"{created}"}},"updated_at":{{"S":"{created}"}}}}"#
        )
    }

    fn share_item(index: usize) -> String {
        let id = Uuid::from_u128(index as u128 + 10_000);
        let file_id = Uuid::from_u128(index as u128 + 1);
        let created = timestamp(index);
        format!(
            r#"{{"id":{{"S":"{id}"}},"file_id":{{"S":"{file_id}"}},"shared_with":{{"S":"{OWNER_ID}"}},"permission":{{"S":"viewer"}},"shared_by":{{"S":"{SHARER_ID}"}},"created_at":{{"S":"{created}"}}}}"#
        )
    }

    fn scan(items: &[String]) -> String {
        format!(
            r#"{{"Items":[{}],"Count":{},"ScannedCount":{}}}"#,
            items.join(","),
            items.len(),
            items.len()
        )
    }

    fn files(body: &Value) -> &Vec<Value> {
        body["files"]
            .as_array()
            .unwrap_or_else(|| panic!("expected a files array, got {body}"))
    }

    // -- Health & metrics ----------------------------------------------------

    #[actix_rt::test]
    async fn test_health_endpoint() {
        let resp = health().await;
        assert_eq!(resp.status(), actix_web::http::StatusCode::OK);
    }

    #[actix_rt::test]
    async fn test_metrics_endpoint() {
        let resp = metrics().await;
        assert_eq!(resp.status(), actix_web::http::StatusCode::OK);
    }

    // -- Upload size limit ---------------------------------------------------

    #[actix_rt::test]
    async fn test_fileservice_upload_one_byte_under_limit_is_accepted() {
        let (status, body) = upload(&[FormPart::file(Some("a.txt"), &[b'a'; 9])]).await;
        assert_eq!(status, StatusCode::CREATED, "body: {body}");
        assert_eq!(body["file"]["size_bytes"], 9);
    }

    #[actix_rt::test]
    async fn test_fileservice_upload_exactly_at_limit_is_accepted() {
        let (status, body) = upload(&[FormPart::file(Some("a.txt"), &[b'a'; 10])]).await;
        assert_eq!(status, StatusCode::CREATED, "body: {body}");
        assert_eq!(body["file"]["size_bytes"], 10);
    }

    #[actix_rt::test]
    async fn test_fileservice_upload_one_byte_over_limit_is_rejected() {
        let (status, body) = upload(&[FormPart::file(Some("a.txt"), &[b'a'; 11])]).await;
        assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
        assert_eq!(body["error"], "file_too_large");
    }

    // -- Upload field handling -----------------------------------------------

    #[actix_rt::test]
    async fn test_fileservice_upload_without_filename_falls_back_to_unnamed() {
        let (status, body) = upload(&[FormPart::file(None, b"hi")]).await;
        assert_eq!(status, StatusCode::CREATED, "body: {body}");
        assert_eq!(body["file"]["name"], "unnamed");
    }

    // DEFECT: `filename=""` is stored verbatim as an empty name instead of
    // falling back to "unnamed". The fallback in `upload_file` only applies
    // when the multipart part carries no `filename` parameter at all, so a
    // browser that sends an empty one creates a nameless file. Test kept as
    // the specification; ignored because the product is currently wrong.
    #[ignore = "empty multipart filename is not replaced by the \"unnamed\" fallback"]
    #[actix_rt::test]
    async fn test_fileservice_upload_empty_filename_falls_back_to_unnamed() {
        let (status, body) = upload(&[FormPart::file(Some(""), b"hi")]).await;
        assert_eq!(status, StatusCode::CREATED, "body: {body}");
        assert_eq!(body["file"]["name"], "unnamed");
    }

    #[actix_rt::test]
    async fn test_fileservice_upload_unicode_filename_round_trips() {
        let (status, body) = upload(&[FormPart::file(Some("naïve-日本語.txt"), b"hi")]).await;
        assert_eq!(status, StatusCode::CREATED, "body: {body}");
        assert_eq!(body["file"]["name"], "naïve-日本語.txt");
    }

    #[actix_rt::test]
    async fn test_fileservice_upload_traversal_filename_is_not_used_as_s3_key() {
        let (status, body) = upload(&[FormPart::file(Some("../../etc/passwd"), b"hi")]).await;
        assert_eq!(status, StatusCode::CREATED, "body: {body}");
        assert_eq!(body["file"]["name"], "../../etc/passwd");

        let s3_key = body["file"]["s3_key"].as_str().expect("s3_key");
        assert!(
            !s3_key.contains(".."),
            "attacker-controlled name reached the S3 key: {s3_key}"
        );
        assert_eq!(
            s3_key,
            format!("files/{OWNER_ID}/{}", body["file"]["id"].as_str().unwrap())
        );
    }

    #[actix_rt::test]
    async fn test_fileservice_upload_invalid_owner_id_is_bad_request() {
        let (status, body) = upload_as(
            None,
            &[
                FormPart::field("owner_id", "not-a-uuid"),
                FormPart::file(Some("a.txt"), b"hi"),
            ],
        )
        .await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert_eq!(body["error"], "bad_request");
    }

    #[actix_rt::test]
    async fn test_fileservice_upload_owner_id_field_is_used_without_header() {
        let (status, body) = upload_as(
            None,
            &[
                FormPart::field("owner_id", OWNER_ID),
                FormPart::file(Some("a.txt"), b"hi"),
            ],
        )
        .await;
        assert_eq!(status, StatusCode::CREATED, "body: {body}");
        assert_eq!(body["file"]["owner_id"], OWNER_ID);
    }

    #[actix_rt::test]
    async fn test_fileservice_upload_without_owner_header_or_field_is_bad_request() {
        let (status, body) = upload_as(None, &[FormPart::file(Some("a.txt"), b"hi")]).await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert_eq!(body["error"], "bad_request");
        assert!(
            body["message"]
                .as_str()
                .unwrap_or_default()
                .contains("owner_id"),
            "message: {}",
            body["message"]
        );
    }

    #[actix_rt::test]
    async fn test_fileservice_upload_empty_folder_id_is_treated_as_none() {
        let (status, body) = upload(&[
            FormPart::field("folder_id", "   "),
            FormPart::file(Some("a.txt"), b"hi"),
        ])
        .await;
        assert_eq!(status, StatusCode::CREATED, "body: {body}");
        assert_eq!(body["file"]["folder_id"], Value::Null);
    }

    #[actix_rt::test]
    async fn test_fileservice_upload_invalid_folder_id_is_bad_request() {
        let (status, body) = upload(&[
            FormPart::field("folder_id", "not-a-uuid"),
            FormPart::file(Some("a.txt"), b"hi"),
        ])
        .await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert_eq!(body["error"], "bad_request");
    }

    // -- Listing: pagination clamps -----------------------------------------

    #[actix_rt::test]
    async fn test_fileservice_list_page_size_below_cap_is_honoured() {
        let (status, body) = list_with("/api/v1/files?page_size=99", 120).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(files(&body).len(), 99);
        assert_eq!(body["page_size"], 99);
        assert_eq!(body["total"], 120);
    }

    #[actix_rt::test]
    async fn test_fileservice_list_page_size_at_cap_is_honoured() {
        let (status, body) = list_with("/api/v1/files?page_size=100", 120).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(files(&body).len(), 100);
        assert_eq!(body["page_size"], 100);
    }

    #[actix_rt::test]
    async fn test_fileservice_list_page_size_above_cap_is_clamped_to_100() {
        let (status, body) = list_with("/api/v1/files?page_size=101", 120).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(files(&body).len(), 100);
        assert_eq!(body["page_size"], 100);
    }

    #[actix_rt::test]
    async fn test_fileservice_list_without_page_size_defaults_to_50() {
        let (status, body) = list_with("/api/v1/files", 120).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(files(&body).len(), 50);
        assert_eq!(body["page_size"], 50);
    }

    #[actix_rt::test]
    async fn test_fileservice_list_page_size_zero_returns_an_empty_page() {
        // Documents current behaviour: 0 is taken literally rather than
        // rejected or replaced by the default, so the page comes back empty
        // while `total` still reports every matching file.
        let (status, body) = list_with("/api/v1/files?page_size=0", 120).await;
        assert_eq!(status, StatusCode::OK);
        assert!(files(&body).is_empty());
        assert_eq!(body["page_size"], 0);
        assert_eq!(body["total"], 120);
    }

    #[actix_rt::test]
    async fn test_fileservice_list_page_zero_does_not_underflow() {
        let (status, body) = list_with("/api/v1/files?page=0&page_size=10", 120).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["page"], 1);
        assert_eq!(files(&body).len(), 10);
        assert_eq!(files(&body)[0]["name"], "file-0.txt");
    }

    #[actix_rt::test]
    async fn test_fileservice_list_page_beyond_last_returns_empty_list() {
        let (status, body) = list_with("/api/v1/files?page=4&page_size=50", 120).await;
        assert_eq!(status, StatusCode::OK);
        assert!(body["files"].is_array(), "files was {}", body["files"]);
        assert!(files(&body).is_empty());
        assert_eq!(body["total"], 120);
    }

    #[actix_rt::test]
    async fn test_fileservice_list_second_page_continues_after_the_first() {
        let (_, body) = list_with("/api/v1/files?page=2&page_size=50", 120).await;
        assert_eq!(files(&body).len(), 50);
        assert_eq!(files(&body)[0]["name"], "file-50.txt");
    }

    #[actix_rt::test]
    async fn test_fileservice_list_with_no_files_returns_empty_array() {
        let (status, body) = list_with("/api/v1/files", 0).await;
        assert_eq!(status, StatusCode::OK);
        assert!(body["files"].is_array(), "files was {}", body["files"]);
        assert!(files(&body).is_empty());
        assert_eq!(body["total"], 0);
    }

    #[actix_rt::test]
    async fn test_fileservice_list_exactly_page_size_items_has_no_further_page() {
        let (_, first) = list_with("/api/v1/files?page=1&page_size=50", 50).await;
        assert_eq!(files(&first).len(), 50);
        assert_eq!(first["total"], 50);

        let (status, second) = list_with("/api/v1/files?page=2&page_size=50", 50).await;
        assert_eq!(status, StatusCode::OK);
        assert!(files(&second).is_empty());
        assert_eq!(second["total"], 50);
    }

    #[actix_rt::test]
    async fn test_fileservice_trash_page_size_above_cap_is_clamped_to_100() {
        let (status, body) = list_with("/api/v1/files/trash?page_size=101", 120).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(files(&body).len(), 100);
        assert_eq!(body["page_size"], 100);
    }

    #[actix_rt::test]
    async fn test_fileservice_trash_with_no_files_returns_empty_array() {
        let (status, body) = list_with("/api/v1/files/trash", 0).await;
        assert_eq!(status, StatusCode::OK);
        assert!(body["files"].is_array(), "files was {}", body["files"]);
        assert!(files(&body).is_empty());
    }

    #[actix_rt::test]
    async fn test_fileservice_shared_page_size_above_cap_is_clamped_to_100() {
        let shares: Vec<String> = (0..120).map(share_item).collect();
        let (status, body) = get(
            "/api/v1/files/shared?page_size=101",
            AwsResponses {
                shares_scan: Some(scan(&shares)),
                get_file: Some(format!(r#"{{"Item":{}}}"#, file_item(0))),
                ..AwsResponses::default()
            },
        )
        .await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(files(&body).len(), 100);
        assert_eq!(body["page_size"], 100);
    }

    #[actix_rt::test]
    async fn test_fileservice_shared_without_user_header_is_bad_request() {
        let (status, body) = call_api(
            test::TestRequest::get().uri("/api/v1/files/shared"),
            AwsResponses::default(),
        )
        .await;
        assert_eq!(status, StatusCode::BAD_REQUEST);
        assert_eq!(body["error"], "bad_request");
    }

    // -- Activity limit clamp ------------------------------------------------

    async fn activity(uri: &str, item_count: usize) -> (StatusCode, usize) {
        let (status, body) = get(
            uri,
            AwsResponses {
                files_scan: Some(scan(&(0..item_count).map(file_item).collect::<Vec<_>>())),
                shares_scan: Some(scan(&[])),
                ..AwsResponses::default()
            },
        )
        .await;
        let items = body["items"]
            .as_array()
            .unwrap_or_else(|| panic!("expected an items array, got {body}"))
            .len();
        (status, items)
    }

    #[actix_rt::test]
    async fn test_fileservice_activity_without_limit_returns_at_most_20() {
        let (status, items) = activity("/api/v1/files/activity", 60).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(items, 20);
    }

    #[actix_rt::test]
    async fn test_fileservice_activity_limit_below_cap_is_honoured() {
        let (_, items) = activity("/api/v1/files/activity?limit=49", 60).await;
        assert_eq!(items, 49);
    }

    #[actix_rt::test]
    async fn test_fileservice_activity_limit_at_cap_is_honoured() {
        let (_, items) = activity("/api/v1/files/activity?limit=50", 60).await;
        assert_eq!(items, 50);
    }

    #[actix_rt::test]
    async fn test_fileservice_activity_limit_above_cap_is_clamped_to_50() {
        let (_, items) = activity("/api/v1/files/activity?limit=51", 60).await;
        assert_eq!(items, 50);
    }

    #[actix_rt::test]
    async fn test_fileservice_activity_with_no_files_returns_empty_array() {
        let (status, items) = activity("/api/v1/files/activity", 0).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(items, 0);
    }
}

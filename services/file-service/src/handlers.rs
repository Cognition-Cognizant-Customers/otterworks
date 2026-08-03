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

/// Register every route the service exposes.
pub fn configure_routes(cfg: &mut web::ServiceConfig) {
    cfg.route("/health", web::get().to(health))
        .route("/metrics", web::get().to(metrics))
        .service(
            web::scope("/api/v1/files")
                .route("/upload", web::post().to(upload_file))
                .route("/shared", web::get().to(list_shared_files))
                .route("/trash", web::get().to(list_trashed))
                .route("/activity", web::get().to(list_activity))
                .route("", web::get().to(list_files))
                .route("/{file_id}", web::get().to(get_file_metadata))
                .route("/{file_id}", web::delete().to(delete_file))
                .route("/{file_id}/download", web::get().to(download_file))
                .route("/{file_id}/move", web::put().to(move_file))
                .route("/{file_id}/rename", web::patch().to(rename_file))
                .route("/{file_id}/versions", web::get().to(list_versions))
                .route("/{file_id}/trash", web::post().to(trash_file))
                .route("/{file_id}/restore", web::post().to(restore_file))
                .route("/{file_id}/share", web::post().to(share_file))
                .route("/{file_id}/share/{user_id}", web::delete().to(remove_share)),
        )
        .service(
            web::scope("/api/v1/folders")
                .route("", web::get().to(list_folders))
                .route("", web::post().to(create_folder))
                .route("/{folder_id}", web::get().to(get_folder))
                .route("/{folder_id}", web::put().to(update_folder))
                .route("/{folder_id}", web::delete().to(delete_folder)),
        );
}

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
    use crate::test_support::{
        dynamo_missing_item, dynamo_ok, expected_request, multipart_body, multipart_content_type,
        s3_error, s3_ok, test_config, test_state, TestState, TEST_BUCKET,
    };
    use actix_web::{test, App};
    use aws_smithy_http_client::test_util::ReplayEvent;
    use serde_json::Value;

    const CHAOS_FLAG: &str = "chaos:file-service:upload_s3_error";
    const CHAOS_BUCKET: &str = "otterworks-files-chaos-nonexistent";

    fn s3_uri(bucket: &str, key: &str) -> String {
        format!("http://s3.local/{bucket}/{key}")
    }

    fn dynamo_uri() -> String {
        "http://dynamodb.local/".to_string()
    }

    /// A DynamoDB `GetItem` response holding one file row.
    fn file_item(file_id: Uuid, owner_id: Uuid, s3_key: &str) -> String {
        let now = Utc::now().to_rfc3339();
        format!(
            r#"{{"Item":{{
                "id":{{"S":"{file_id}"}},
                "name":{{"S":"report.pdf"}},
                "mime_type":{{"S":"application/pdf"}},
                "size_bytes":{{"N":"12"}},
                "s3_key":{{"S":"{s3_key}"}},
                "owner_id":{{"S":"{owner_id}"}},
                "version":{{"N":"1"}},
                "is_trashed":{{"BOOL":false}},
                "created_at":{{"S":"{now}"}},
                "updated_at":{{"S":"{now}"}}
            }}}}"#
        )
    }

    async fn call(state: &TestState, req: test::TestRequest) -> actix_web::dev::ServiceResponse {
        let app = test::init_service(App::new().configure(|cfg| state.register(cfg))).await;
        test::call_service(&app, req.to_request()).await
    }

    fn upload_request(body: Vec<u8>, user_id: Option<Uuid>) -> test::TestRequest {
        let mut req = test::TestRequest::post()
            .uri("/api/v1/files/upload")
            .insert_header(("content-type", multipart_content_type()))
            .set_payload(body);
        if let Some(user_id) = user_id {
            req = req.insert_header(("X-User-ID", user_id.to_string()));
        }
        req
    }

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

    // -- Upload --

    #[actix_rt::test]
    async fn upload_stores_blob_then_metadata_and_version() {
        let owner = Uuid::new_v4();
        let (state, s3_http, dynamo_http) = test_state(
            vec![ReplayEvent::new(
                expected_request(&s3_uri(TEST_BUCKET, "files")),
                s3_ok(),
            )],
            vec![
                ReplayEvent::new(expected_request(&dynamo_uri()), dynamo_ok("{}")),
                ReplayEvent::new(expected_request(&dynamo_uri()), dynamo_ok("{}")),
            ],
            &[],
        )
        .await;

        let body = multipart_body(
            Some(("report.pdf", "application/pdf", b"hello world!")),
            &[("owner_id", &owner.to_string())],
        );
        let resp = call(&state, upload_request(body, None)).await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::CREATED);
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["file"]["name"], "report.pdf");
        assert_eq!(json["file"]["mime_type"], "application/pdf");
        assert_eq!(json["file"]["size_bytes"], 12);
        assert_eq!(json["file"]["owner_id"], owner.to_string());
        assert_eq!(json["file"]["version"], 1);

        let s3_request = s3_http.actual_requests().next().unwrap();
        assert_eq!(s3_request.method(), "PUT");
        assert!(
            s3_request
                .uri()
                .starts_with(&s3_uri(TEST_BUCKET, &format!("files/{owner}/"))),
            "unexpected S3 uri: {}",
            s3_request.uri()
        );
        assert_eq!(s3_request.body().bytes(), Some(&b"hello world!"[..]));
        assert_eq!(dynamo_http.actual_requests().count(), 2);
    }

    #[actix_rt::test]
    async fn upload_prefers_the_gateway_user_header_over_the_form_field() {
        let header_owner = Uuid::new_v4();
        let form_owner = Uuid::new_v4();
        let (state, s3_http, _dynamo_http) = test_state(
            vec![ReplayEvent::new(
                expected_request(&s3_uri(TEST_BUCKET, "files")),
                s3_ok(),
            )],
            vec![
                ReplayEvent::new(expected_request(&dynamo_uri()), dynamo_ok("{}")),
                ReplayEvent::new(expected_request(&dynamo_uri()), dynamo_ok("{}")),
            ],
            &[],
        )
        .await;

        let body = multipart_body(
            Some(("report.pdf", "application/pdf", b"hello world!")),
            &[("owner_id", &form_owner.to_string())],
        );
        let resp = call(&state, upload_request(body, Some(header_owner))).await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::CREATED);
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["file"]["owner_id"], header_owner.to_string());
        assert!(s3_http
            .actual_requests()
            .next()
            .unwrap()
            .uri()
            .contains(&header_owner.to_string()));
    }

    #[actix_rt::test]
    async fn upload_chaos_flag_targets_a_nonexistent_bucket_and_returns_a_storage_error() {
        let owner = Uuid::new_v4();
        let (state, s3_http, dynamo_http) = test_state(
            vec![ReplayEvent::new(
                expected_request(&s3_uri(CHAOS_BUCKET, "files")),
                s3_error(404, "NoSuchBucket"),
            )],
            vec![],
            &[CHAOS_FLAG],
        )
        .await;

        let body = multipart_body(
            Some(("report.pdf", "application/pdf", b"hello world!")),
            &[("owner_id", &owner.to_string())],
        );
        let resp = call(&state, upload_request(body, None)).await;

        assert_eq!(
            resp.status(),
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR
        );
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["error"], "storage_error");
        assert!(
            json["message"].as_str().unwrap().contains("upload failed"),
            "unexpected message: {}",
            json["message"]
        );

        // The blob write was redirected to the chaos bucket and nothing was persisted.
        assert!(s3_http
            .actual_requests()
            .next()
            .unwrap()
            .uri()
            .starts_with(&s3_uri(CHAOS_BUCKET, "files/")));
        assert_eq!(dynamo_http.actual_requests().count(), 0);
    }

    #[actix_rt::test]
    async fn upload_without_the_chaos_flag_uses_the_configured_bucket() {
        let owner = Uuid::new_v4();
        let (state, s3_http, _dynamo_http) = test_state(
            vec![ReplayEvent::new(
                expected_request(&s3_uri(TEST_BUCKET, "files")),
                s3_ok(),
            )],
            vec![
                ReplayEvent::new(expected_request(&dynamo_uri()), dynamo_ok("{}")),
                ReplayEvent::new(expected_request(&dynamo_uri()), dynamo_ok("{}")),
            ],
            // An unrelated chaos flag must not divert the upload.
            &["chaos:file-service:some_other_flag"],
        )
        .await;

        let body = multipart_body(
            Some(("report.pdf", "application/pdf", b"hello world!")),
            &[("owner_id", &owner.to_string())],
        );
        let resp = call(&state, upload_request(body, None)).await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::CREATED);
        assert!(s3_http
            .actual_requests()
            .next()
            .unwrap()
            .uri()
            .starts_with(&s3_uri(TEST_BUCKET, "files/")));
    }

    #[actix_rt::test]
    async fn upload_without_an_owner_is_a_bad_request() {
        let (state, s3_http, _dynamo_http) = test_state(vec![], vec![], &[]).await;

        let body = multipart_body(Some(("report.pdf", "application/pdf", b"data")), &[]);
        let resp = call(&state, upload_request(body, None)).await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::BAD_REQUEST);
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["error"], "bad_request");
        assert_eq!(s3_http.actual_requests().count(), 0);
    }

    #[actix_rt::test]
    async fn upload_without_a_file_part_is_a_bad_request() {
        let (state, _s3_http, _dynamo_http) = test_state(vec![], vec![], &[]).await;

        let owner = Uuid::new_v4();
        let body = multipart_body(None, &[("owner_id", &owner.to_string())]);
        let resp = call(&state, upload_request(body, None)).await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::BAD_REQUEST);
    }

    #[actix_rt::test]
    async fn upload_over_the_size_limit_is_rejected() {
        let (mut state, s3_http, _dynamo_http) = test_state(vec![], vec![], &[]).await;
        state.config = web::Data::new(test_config(4));

        let owner = Uuid::new_v4();
        let body = multipart_body(
            Some(("report.pdf", "application/pdf", b"far too many bytes")),
            &[("owner_id", &owner.to_string())],
        );
        let resp = call(&state, upload_request(body, None)).await;

        assert_eq!(
            resp.status(),
            actix_web::http::StatusCode::PAYLOAD_TOO_LARGE
        );
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["error"], "file_too_large");
        assert_eq!(s3_http.actual_requests().count(), 0);
    }

    // -- Download --

    #[actix_rt::test]
    async fn download_returns_a_presigned_url_for_the_stored_key() {
        let file_id = Uuid::new_v4();
        let owner = Uuid::new_v4();
        let key = format!("files/{owner}/{file_id}");
        let (state, s3_http, _dynamo_http) = test_state(
            vec![],
            vec![ReplayEvent::new(
                expected_request(&dynamo_uri()),
                dynamo_ok(&file_item(file_id, owner, &key)),
            )],
            &[],
        )
        .await;

        let resp = call(
            &state,
            test::TestRequest::get().uri(&format!("/api/v1/files/{file_id}/download")),
        )
        .await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::OK);
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["expires_in_secs"], 3600);
        let url = json["url"].as_str().unwrap();
        assert!(url.starts_with(&s3_uri(TEST_BUCKET, &key)), "url: {url}");
        assert!(url.contains("X-Amz-Signature="), "url: {url}");
        // Presigning is offline: no request reaches S3.
        assert_eq!(s3_http.actual_requests().count(), 0);
    }

    #[actix_rt::test]
    async fn download_of_an_unknown_file_is_a_404() {
        let (state, _s3_http, _dynamo_http) = test_state(
            vec![],
            vec![ReplayEvent::new(
                expected_request(&dynamo_uri()),
                dynamo_missing_item(),
            )],
            &[],
        )
        .await;

        let file_id = Uuid::new_v4();
        let resp = call(
            &state,
            test::TestRequest::get().uri(&format!("/api/v1/files/{file_id}/download")),
        )
        .await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::NOT_FOUND);
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["error"], "file_not_found");
    }

    #[actix_rt::test]
    async fn download_with_a_malformed_id_is_a_bad_request() {
        let (state, _s3_http, _dynamo_http) = test_state(vec![], vec![], &[]).await;

        let resp = call(
            &state,
            test::TestRequest::get().uri("/api/v1/files/not-a-uuid/download"),
        )
        .await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::BAD_REQUEST);
    }

    // -- Delete --

    #[actix_rt::test]
    async fn delete_removes_metadata_then_the_blob() {
        let file_id = Uuid::new_v4();
        let owner = Uuid::new_v4();
        let key = format!("files/{owner}/{file_id}");
        let (state, s3_http, dynamo_http) = test_state(
            vec![ReplayEvent::new(
                expected_request(&s3_uri(TEST_BUCKET, &key)),
                s3_ok(),
            )],
            vec![
                ReplayEvent::new(
                    expected_request(&dynamo_uri()),
                    dynamo_ok(&file_item(file_id, owner, &key)),
                ),
                ReplayEvent::new(expected_request(&dynamo_uri()), dynamo_ok("{}")),
            ],
            &[],
        )
        .await;

        let resp = call(
            &state,
            test::TestRequest::delete().uri(&format!("/api/v1/files/{file_id}")),
        )
        .await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::NO_CONTENT);
        assert_eq!(dynamo_http.actual_requests().count(), 2);
        let s3_request = s3_http.actual_requests().next().unwrap();
        assert_eq!(s3_request.method(), "DELETE");
        assert!(s3_request.uri().starts_with(&s3_uri(TEST_BUCKET, &key)));
    }

    #[actix_rt::test]
    async fn delete_of_an_unknown_file_is_a_404() {
        let (state, s3_http, _dynamo_http) = test_state(
            vec![],
            vec![ReplayEvent::new(
                expected_request(&dynamo_uri()),
                dynamo_missing_item(),
            )],
            &[],
        )
        .await;

        let file_id = Uuid::new_v4();
        let resp = call(
            &state,
            test::TestRequest::delete().uri(&format!("/api/v1/files/{file_id}")),
        )
        .await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::NOT_FOUND);
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["error"], "file_not_found");
        assert_eq!(s3_http.actual_requests().count(), 0);
    }

    #[actix_rt::test]
    async fn delete_surfaces_an_s3_failure_as_a_storage_error() {
        let file_id = Uuid::new_v4();
        let owner = Uuid::new_v4();
        let key = format!("files/{owner}/{file_id}");
        let (state, _s3_http, _dynamo_http) = test_state(
            vec![ReplayEvent::new(
                expected_request(&s3_uri(TEST_BUCKET, &key)),
                s3_error(403, "AccessDenied"),
            )],
            vec![
                ReplayEvent::new(
                    expected_request(&dynamo_uri()),
                    dynamo_ok(&file_item(file_id, owner, &key)),
                ),
                ReplayEvent::new(expected_request(&dynamo_uri()), dynamo_ok("{}")),
            ],
            &[],
        )
        .await;

        let resp = call(
            &state,
            test::TestRequest::delete().uri(&format!("/api/v1/files/{file_id}")),
        )
        .await;

        assert_eq!(
            resp.status(),
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR
        );
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["error"], "storage_error");
    }

    // -- Metadata --

    #[actix_rt::test]
    async fn get_file_metadata_returns_the_file_with_its_shares() {
        let file_id = Uuid::new_v4();
        let owner = Uuid::new_v4();
        let key = format!("files/{owner}/{file_id}");
        let (state, _s3_http, _dynamo_http) = test_state(
            vec![],
            vec![
                ReplayEvent::new(
                    expected_request(&dynamo_uri()),
                    dynamo_ok(&file_item(file_id, owner, &key)),
                ),
                // scan of the shares table
                ReplayEvent::new(
                    expected_request(&dynamo_uri()),
                    dynamo_ok(r#"{"Items":[]}"#),
                ),
            ],
            &[],
        )
        .await;

        let resp = call(
            &state,
            test::TestRequest::get().uri(&format!("/api/v1/files/{file_id}")),
        )
        .await;

        assert_eq!(resp.status(), actix_web::http::StatusCode::OK);
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["id"], file_id.to_string());
        assert_eq!(json["s3_key"], key);
        assert_eq!(json["shared_with"].as_array().unwrap().len(), 0);
    }

    #[actix_rt::test]
    async fn metadata_failures_surface_as_metadata_errors() {
        let (state, _s3_http, _dynamo_http) = test_state(
            vec![],
            vec![ReplayEvent::new(
                expected_request(&dynamo_uri()),
                crate::test_support::dynamo_error("ResourceNotFoundException"),
            )],
            &[],
        )
        .await;

        let file_id = Uuid::new_v4();
        let resp = call(
            &state,
            test::TestRequest::get().uri(&format!("/api/v1/files/{file_id}/download")),
        )
        .await;

        assert_eq!(
            resp.status(),
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR
        );
        let json: Value = test::read_body_json(resp).await;
        assert_eq!(json["error"], "metadata_error");
    }
}

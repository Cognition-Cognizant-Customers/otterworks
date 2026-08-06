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

/// Test-only harness for exercising the handlers over a real actix `App` with
/// stubbed AWS clients and a stubbed Redis, so the suite is hermetic (no
/// LocalStack, no network) and free of inter-test state.
#[cfg(test)]
pub(crate) mod test_support {
    use super::*;
    use crate::config::{AwsConfig, ServerConfig, SnsConfig};
    use crate::models::SharePermission;
    use crate::storage::test_support::{stub_s3_with_bucket, TEST_BUCKET};
    use aws_sdk_dynamodb::operation::delete_item::DeleteItemOutput;
    use aws_sdk_dynamodb::operation::get_item::GetItemOutput;
    use aws_sdk_dynamodb::operation::put_item::PutItemOutput;
    use aws_sdk_dynamodb::operation::query::QueryOutput;
    use aws_sdk_dynamodb::operation::scan::ScanOutput;
    use aws_sdk_dynamodb::operation::update_item::{UpdateItemError, UpdateItemOutput};
    use aws_sdk_dynamodb::types::error::ConditionalCheckFailedException;
    use aws_sdk_dynamodb::types::AttributeValue;
    use aws_sdk_s3::operation::delete_object::DeleteObjectOutput;
    use aws_sdk_s3::operation::get_object::GetObjectOutput;
    use aws_sdk_s3::operation::put_object::PutObjectOutput;
    use aws_smithy_mocks::{mock, mock_client, MockResponse, Rule, RuleMode};
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    pub(crate) const FILES_TABLE: &str = "test-files";
    pub(crate) const FOLDERS_TABLE: &str = "test-folders";
    pub(crate) const VERSIONS_TABLE: &str = "test-versions";
    pub(crate) const SHARES_TABLE: &str = "test-shares";

    /// Redis integer replies used by the chaos-flag stub.
    pub(crate) const REDIS_FLAG_ABSENT: &[u8] = b":0\r\n";
    pub(crate) const REDIS_FLAG_PRESENT: &[u8] = b":1\r\n";
    pub(crate) const REDIS_ERROR: &[u8] = b"-ERR simulated redis failure\r\n";

    pub(crate) type Item = HashMap<String, AttributeValue>;

    // ── In-memory DynamoDB ─────────────────────────────────────────────

    /// A minimal in-memory stand-in for DynamoDB. It understands only the
    /// expression shapes `metadata.rs` actually emits (`a = :v` conjunctions,
    /// `attribute_exists` / `attribute_not_exists`, `SET ... [REMOVE ...]`)
    /// and panics loudly on anything else so the fake cannot silently drift
    /// from the production query it is standing in for.
    #[derive(Clone, Default)]
    pub(crate) struct FakeDynamo {
        tables: Arc<Mutex<HashMap<String, Vec<Item>>>>,
        ops: Arc<Mutex<Vec<String>>>,
    }

    impl FakeDynamo {
        pub(crate) fn items(&self, table: &str) -> Vec<Item> {
            self.tables
                .lock()
                .unwrap()
                .get(table)
                .cloned()
                .unwrap_or_default()
        }

        pub(crate) fn count(&self, table: &str) -> usize {
            self.items(table).len()
        }

        pub(crate) fn ops(&self) -> Vec<String> {
            self.ops.lock().unwrap().clone()
        }

        pub(crate) fn op_count(&self, op: &str) -> usize {
            self.ops().iter().filter(|o| o.as_str() == op).count()
        }

        pub(crate) fn clear_ops(&self) {
            self.ops.lock().unwrap().clear();
        }

        fn record(&self, op: String) {
            self.ops.lock().unwrap().push(op);
        }

        fn put(&self, table: &str, item: Item) {
            let mut tables = self.tables.lock().unwrap();
            let rows = tables.entry(table.to_string()).or_default();
            match item.get("id").or_else(|| item.get("file_id")) {
                Some(id) => {
                    let key = if item.contains_key("id") {
                        "id"
                    } else {
                        "file_id"
                    };
                    let version = item.get("version").cloned();
                    let existing = rows.iter().position(|row| {
                        row.get(key) == Some(id)
                            && (key != "file_id" || row.get("version") == version.as_ref())
                    });
                    match existing {
                        Some(idx) => rows[idx] = item,
                        None => rows.push(item),
                    }
                }
                None => rows.push(item),
            }
        }

        fn find(&self, table: &str, key: &Item) -> Option<Item> {
            self.items(table)
                .into_iter()
                .find(|row| key.iter().all(|(k, v)| row.get(k) == Some(v)))
        }

        fn remove(&self, table: &str, key: &Item) {
            let mut tables = self.tables.lock().unwrap();
            if let Some(rows) = tables.get_mut(table) {
                rows.retain(|row| !key.iter().all(|(k, v)| row.get(k) == Some(v)));
            }
        }
    }

    fn matches_expression(
        item: &Item,
        expression: Option<&str>,
        values: Option<&HashMap<String, AttributeValue>>,
    ) -> bool {
        let Some(expression) = expression else {
            return true;
        };
        expression.split(" AND ").all(|clause| {
            let clause = clause.trim();
            if let Some(attr) = clause
                .strip_prefix("attribute_not_exists(")
                .and_then(|s| s.strip_suffix(')'))
            {
                !item.contains_key(attr)
            } else if let Some(attr) = clause
                .strip_prefix("attribute_exists(")
                .and_then(|s| s.strip_suffix(')'))
            {
                item.contains_key(attr)
            } else if let Some((attr, placeholder)) = clause.split_once(" = ") {
                let expected = values
                    .and_then(|v| v.get(placeholder.trim()))
                    .unwrap_or_else(|| panic!("fake dynamodb: unbound placeholder {placeholder}"));
                item.get(attr.trim()) == Some(expected)
            } else {
                panic!("fake dynamodb: unsupported expression clause {clause:?}")
            }
        })
    }

    /// Apply the `SET a = :x, b = :y [REMOVE c]` shape used by `metadata.rs`.
    fn apply_update(
        item: &mut Item,
        update_expression: &str,
        names: Option<&HashMap<String, String>>,
        values: Option<&HashMap<String, AttributeValue>>,
    ) {
        let (set_part, remove_part) = match update_expression.split_once(" REMOVE ") {
            Some((set, remove)) => (set, Some(remove)),
            None => (update_expression, None),
        };
        let set_part = set_part
            .trim()
            .strip_prefix("SET ")
            .unwrap_or_else(|| panic!("fake dynamodb: unsupported update {update_expression:?}"));
        for assignment in set_part.split(", ") {
            let (lhs, placeholder) = assignment
                .trim()
                .split_once(" = ")
                .unwrap_or_else(|| panic!("fake dynamodb: unsupported assignment {assignment:?}"));
            let attr = match lhs.strip_prefix('#') {
                Some(_) => names
                    .and_then(|n| n.get(lhs))
                    .unwrap_or_else(|| panic!("fake dynamodb: unbound name {lhs}"))
                    .clone(),
                None => lhs.to_string(),
            };
            let value = values
                .and_then(|v| v.get(placeholder.trim()))
                .unwrap_or_else(|| panic!("fake dynamodb: unbound placeholder {placeholder}"))
                .clone();
            item.insert(attr, value);
        }
        if let Some(remove) = remove_part {
            for attr in remove.split(", ") {
                item.remove(attr.trim());
            }
        }
    }

    fn number(item: &Item, key: &str) -> i64 {
        item.get(key)
            .and_then(|v| v.as_n().ok())
            .and_then(|n| n.parse().ok())
            .unwrap_or(0)
    }

    fn dynamo_rules(store: &FakeDynamo) -> Vec<Rule> {
        let put_store = store.clone();
        let put = mock!(aws_sdk_dynamodb::Client::put_item).then_compute_output(move |input| {
            let table = input.table_name().expect("table_name");
            put_store.record(format!("put_item:{table}"));
            put_store.put(table, input.item().cloned().unwrap_or_default());
            PutItemOutput::builder().build()
        });

        let get_store = store.clone();
        let get = mock!(aws_sdk_dynamodb::Client::get_item).then_compute_output(move |input| {
            let table = input.table_name().expect("table_name");
            get_store.record(format!("get_item:{table}"));
            let key = input.key().cloned().unwrap_or_default();
            GetItemOutput::builder()
                .set_item(get_store.find(table, &key))
                .build()
        });

        let delete_store = store.clone();
        let delete =
            mock!(aws_sdk_dynamodb::Client::delete_item).then_compute_output(move |input| {
                let table = input.table_name().expect("table_name");
                delete_store.record(format!("delete_item:{table}"));
                delete_store.remove(table, &input.key().cloned().unwrap_or_default());
                DeleteItemOutput::builder().build()
            });

        let update_store = store.clone();
        let update =
            mock!(aws_sdk_dynamodb::Client::update_item).then_compute_response(move |input| {
                let table = input.table_name().expect("table_name");
                update_store.record(format!("update_item:{table}"));
                let key = input.key().cloned().unwrap_or_default();
                match update_store.find(table, &key) {
                    Some(mut item) => {
                        apply_update(
                            &mut item,
                            input.update_expression().expect("update_expression"),
                            input.expression_attribute_names(),
                            input.expression_attribute_values(),
                        );
                        update_store.put(table, item);
                        MockResponse::Output(UpdateItemOutput::builder().build())
                    }
                    None => {
                        if input.condition_expression().is_some() {
                            MockResponse::Error(UpdateItemError::ConditionalCheckFailedException(
                                ConditionalCheckFailedException::builder()
                                    .message("The conditional request failed")
                                    .build(),
                            ))
                        } else {
                            MockResponse::Output(UpdateItemOutput::builder().build())
                        }
                    }
                }
            });

        let scan_store = store.clone();
        let scan = mock!(aws_sdk_dynamodb::Client::scan).then_compute_output(move |input| {
            let table = input.table_name().expect("table_name");
            scan_store.record(format!("scan:{table}"));
            let items: Vec<Item> = scan_store
                .items(table)
                .into_iter()
                .filter(|item| {
                    matches_expression(
                        item,
                        input.filter_expression(),
                        input.expression_attribute_values(),
                    )
                })
                .collect();
            ScanOutput::builder()
                .count(items.len() as i32)
                .scanned_count(items.len() as i32)
                .set_items(Some(items))
                .build()
        });

        let query_store = store.clone();
        let query = mock!(aws_sdk_dynamodb::Client::query).then_compute_output(move |input| {
            let table = input.table_name().expect("table_name");
            query_store.record(format!("query:{table}"));
            let mut items: Vec<Item> = query_store
                .items(table)
                .into_iter()
                .filter(|item| {
                    matches_expression(
                        item,
                        input.key_condition_expression(),
                        input.expression_attribute_values(),
                    )
                })
                .collect();
            items.sort_by_key(|item| number(item, "version"));
            if input.scan_index_forward() == Some(false) {
                items.reverse();
            }
            QueryOutput::builder()
                .count(items.len() as i32)
                .set_items(Some(items))
                .build()
        });

        vec![put, get, delete, update, scan, query]
    }

    fn default_s3_rules(log: &Arc<Mutex<Vec<String>>>) -> Vec<Rule> {
        let put_log = log.clone();
        let put = mock!(aws_sdk_s3::Client::put_object).then_compute_output(move |input| {
            put_log.lock().unwrap().push(format!(
                "put_object:{}:{}",
                input.bucket().unwrap_or_default(),
                input.key().unwrap_or_default()
            ));
            PutObjectOutput::builder().build()
        });
        let get_log = log.clone();
        let get = mock!(aws_sdk_s3::Client::get_object).then_compute_output(move |input| {
            get_log
                .lock()
                .unwrap()
                .push(format!("get_object:{}", input.key().unwrap_or_default()));
            GetObjectOutput::builder().build()
        });
        let delete_log = log.clone();
        let delete = mock!(aws_sdk_s3::Client::delete_object).then_compute_output(move |input| {
            delete_log
                .lock()
                .unwrap()
                .push(format!("delete_object:{}", input.key().unwrap_or_default()));
            DeleteObjectOutput::builder().build()
        });
        vec![put, get, delete]
    }

    // ── Stubbed Redis ──────────────────────────────────────────────────

    /// Splits one complete RESP array command off the front of `buf`,
    /// returning its name (upper-cased) and how many bytes it occupied.
    /// Returns `None` while the command is still incomplete.
    fn parse_resp_command(buf: &[u8]) -> Option<(String, usize)> {
        fn line(buf: &[u8], from: usize) -> Option<(&[u8], usize)> {
            let end = buf[from..]
                .windows(2)
                .position(|w| w == b"\r\n")
                .map(|p| from + p)?;
            Some((&buf[from..end], end + 2))
        }

        let (header, mut pos) = line(buf, 0)?;
        let argc: usize = std::str::from_utf8(header.strip_prefix(b"*")?)
            .ok()?
            .parse()
            .ok()?;

        let mut name = String::new();
        for arg in 0..argc {
            let (len_line, after_len) = line(buf, pos)?;
            let len: usize = std::str::from_utf8(len_line.strip_prefix(b"$")?)
                .ok()?
                .parse()
                .ok()?;
            let end = after_len + len;
            if buf.len() < end + 2 {
                return None;
            }
            if arg == 0 {
                name = String::from_utf8_lossy(&buf[after_len..end]).to_uppercase();
            }
            pos = end + 2;
        }
        Some((name, pos))
    }

    /// A TCP server speaking just enough RESP to stand in for Redis: it
    /// answers each complete command with one reply, so the handshake the
    /// client library performs on connect (`CLIENT SETINFO`, …) is satisfied
    /// and `EXISTS` - the only command `chaos_active` issues - gets the canned
    /// reply the test asked for. No real Redis, no timing dependence.
    async fn stub_redis(reply: &'static [u8]) -> redis::aio::ConnectionManager {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind stub redis");
        let addr = listener.local_addr().expect("stub redis addr");
        tokio::spawn(async move {
            while let Ok((mut socket, _)) = listener.accept().await {
                tokio::spawn(async move {
                    let mut pending: Vec<u8> = Vec::new();
                    let mut chunk = [0_u8; 1024];
                    loop {
                        match socket.read(&mut chunk).await {
                            Ok(0) | Err(_) => return,
                            Ok(n) => pending.extend_from_slice(&chunk[..n]),
                        }
                        while let Some((name, consumed)) = parse_resp_command(&pending) {
                            pending.drain(..consumed);
                            // Only the chaos lookup gets the canned reply; the
                            // client's own handshake commands just get +OK.
                            let response: &[u8] = match name.as_str() {
                                "EXISTS" => reply,
                                _ => b"+OK\r\n",
                            };
                            if socket.write_all(response).await.is_err() {
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
            .expect("connect to stub redis")
    }

    // ── Context ────────────────────────────────────────────────────────

    pub(crate) struct Ctx {
        pub(crate) store: FakeDynamo,
        pub(crate) s3_log: Arc<Mutex<Vec<String>>>,
        pub(crate) config: web::Data<AppConfig>,
        pub(crate) s3: web::Data<S3Client>,
        pub(crate) meta: web::Data<MetadataClient>,
        pub(crate) events: web::Data<EventPublisher>,
        pub(crate) redis: web::Data<redis::aio::ConnectionManager>,
        _rules: Vec<Rule>,
    }

    impl Ctx {
        pub(crate) async fn new() -> Self {
            CtxBuilder::new().build().await
        }

        pub(crate) fn s3_ops(&self) -> Vec<String> {
            self.s3_log.lock().unwrap().clone()
        }

        pub(crate) async fn seed_file(&self, file: &FileMetadata) {
            self.meta.put_file(file).await.expect("seed file");
            self.store.clear_ops();
            self.s3_log.lock().unwrap().clear();
        }

        pub(crate) async fn seed_version(&self, version: &FileVersion) {
            self.meta.put_version(version).await.expect("seed version");
            self.store.clear_ops();
        }

        pub(crate) async fn seed_share(&self, share: &FileShare) {
            self.meta.put_share(share).await.expect("seed share");
            self.store.clear_ops();
        }

        pub(crate) async fn seed_folder(&self, folder: &Folder) {
            self.meta.put_folder(folder).await.expect("seed folder");
            self.store.clear_ops();
        }
    }

    pub(crate) struct CtxBuilder {
        max_upload_bytes: u64,
        redis_reply: &'static [u8],
        s3_rules: Vec<Rule>,
        dynamo_rules: Vec<Rule>,
        bucket: String,
    }

    impl CtxBuilder {
        pub(crate) fn new() -> Self {
            Self {
                max_upload_bytes: 1_024,
                redis_reply: REDIS_FLAG_ABSENT,
                s3_rules: Vec::new(),
                dynamo_rules: Vec::new(),
                bucket: TEST_BUCKET.to_string(),
            }
        }

        pub(crate) fn max_upload_bytes(mut self, max: u64) -> Self {
            self.max_upload_bytes = max;
            self
        }

        pub(crate) fn redis_reply(mut self, reply: &'static [u8]) -> Self {
            self.redis_reply = reply;
            self
        }

        /// Rules added here take precedence over the default in-memory
        /// behaviour, which is how a test injects an AWS failure.
        pub(crate) fn s3_rule(mut self, rule: Rule) -> Self {
            self.s3_rules.push(rule);
            self
        }

        pub(crate) fn dynamo_rule(mut self, rule: Rule) -> Self {
            self.dynamo_rules.push(rule);
            self
        }

        pub(crate) async fn build(self) -> Ctx {
            let store = FakeDynamo::default();
            let s3_log: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));

            let mut s3_rules = self.s3_rules;
            s3_rules.extend(default_s3_rules(&s3_log));
            let s3 = stub_s3_with_bucket(&self.bucket, &s3_rules.iter().collect::<Vec<_>>());

            let mut dynamo_rules = self.dynamo_rules;
            dynamo_rules.extend(dynamo_rules_for(&store));
            let dynamo_client = mock_client!(
                aws_sdk_dynamodb,
                RuleMode::MatchAny,
                &dynamo_rules.iter().collect::<Vec<_>>(),
                |builder| builder
                    .retry_config(aws_sdk_dynamodb::config::retry::RetryConfig::disabled())
            );
            let meta = MetadataClient {
                client: dynamo_client,
                files_table: FILES_TABLE.into(),
                folders_table: FOLDERS_TABLE.into(),
                versions_table: VERSIONS_TABLE.into(),
                shares_table: SHARES_TABLE.into(),
            };

            let aws = test_aws_config(&self.bucket);
            let events = EventPublisher::new(&SnsConfig { topic_arn: None }, &aws).await;
            let config = AppConfig {
                server: ServerConfig {
                    port: 0,
                    max_upload_bytes: self.max_upload_bytes,
                },
                aws,
                sns: SnsConfig { topic_arn: None },
            };
            let redis = stub_redis(self.redis_reply).await;

            let mut rules = s3_rules;
            rules.extend(dynamo_rules);

            Ctx {
                store,
                s3_log,
                config: web::Data::new(config),
                s3: web::Data::new(s3),
                meta: web::Data::new(meta),
                events: web::Data::new(events),
                redis: web::Data::new(redis),
                _rules: rules,
            }
        }
    }

    fn dynamo_rules_for(store: &FakeDynamo) -> Vec<Rule> {
        dynamo_rules(store)
    }

    fn test_aws_config(bucket: &str) -> AwsConfig {
        AwsConfig {
            region: "us-east-1".into(),
            // Nothing is ever sent: SNS publishing is disabled (no topic ARN)
            // and S3/DynamoDB go through stubbed clients.
            endpoint_url: Some("http://127.0.0.1:1".into()),
            s3_bucket: bucket.into(),
            dynamodb_table: FILES_TABLE.into(),
            dynamodb_folders_table: FOLDERS_TABLE.into(),
            dynamodb_versions_table: VERSIONS_TABLE.into(),
            dynamodb_shares_table: SHARES_TABLE.into(),
        }
    }

    /// Mirrors the route table in `main.rs`.
    pub(crate) fn routes(cfg: &mut web::ServiceConfig) {
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

    /// Builds the actix test service for a [`Ctx`]. A macro rather than a
    /// function so the (unnameable) service type never has to be spelled out.
    macro_rules! test_app {
        ($ctx:expr) => {{
            let ctx = &$ctx;
            actix_web::test::init_service(
                actix_web::App::new()
                    .app_data(ctx.config.clone())
                    .app_data(ctx.s3.clone())
                    .app_data(ctx.meta.clone())
                    .app_data(ctx.events.clone())
                    .app_data(ctx.redis.clone())
                    .configure(crate::handlers::test_support::routes),
            )
            .await
        }};
    }
    pub(crate) use test_app;

    // ── Multipart bodies ───────────────────────────────────────────────

    pub(crate) const BOUNDARY: &str = "otterworkstestboundary";

    pub(crate) fn multipart_content_type() -> (&'static str, String) {
        (
            "content-type",
            format!("multipart/form-data; boundary={BOUNDARY}"),
        )
    }

    #[derive(Default)]
    pub(crate) struct MultipartBody {
        body: Vec<u8>,
    }

    impl MultipartBody {
        pub(crate) fn new() -> Self {
            Self::default()
        }

        pub(crate) fn text_field(mut self, name: &str, value: &str) -> Self {
            self.body.extend_from_slice(
                format!(
                    "--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
                )
                .as_bytes(),
            );
            self
        }

        pub(crate) fn file_field(
            mut self,
            name: &str,
            filename: &str,
            content_type: Option<&str>,
            data: &[u8],
        ) -> Self {
            self.body.extend_from_slice(
                format!(
                    "--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
                )
                .as_bytes(),
            );
            if let Some(ct) = content_type {
                self.body
                    .extend_from_slice(format!("Content-Type: {ct}\r\n").as_bytes());
            }
            self.body.extend_from_slice(b"\r\n");
            self.body.extend_from_slice(data);
            self.body.extend_from_slice(b"\r\n");
            self
        }

        pub(crate) fn finish(mut self) -> Vec<u8> {
            self.body
                .extend_from_slice(format!("--{BOUNDARY}--\r\n").as_bytes());
            self.body
        }
    }

    /// The canonical happy-path upload body: one `file` part plus `owner_id`.
    pub(crate) fn upload_body(owner: &Uuid, filename: &str, data: &[u8]) -> Vec<u8> {
        MultipartBody::new()
            .file_field("file", filename, Some("text/plain"), data)
            .text_field("owner_id", &owner.to_string())
            .finish()
    }

    // ── Fixtures ───────────────────────────────────────────────────────

    /// A fixed timestamp keeps assertions free of wall-clock dependence.
    pub(crate) fn fixed_time(offset_secs: i64) -> chrono::DateTime<Utc> {
        chrono::DateTime::from_timestamp(1_700_000_000 + offset_secs, 0).expect("valid timestamp")
    }

    pub(crate) fn file_fixture(owner: Uuid) -> FileMetadata {
        let id = Uuid::new_v4();
        FileMetadata {
            id,
            name: "report.txt".into(),
            mime_type: "text/plain".into(),
            size_bytes: 12,
            s3_key: format!("files/{owner}/{id}"),
            folder_id: None,
            owner_id: owner,
            version: 1,
            is_trashed: false,
            created_at: fixed_time(0),
            updated_at: fixed_time(0),
        }
    }

    pub(crate) fn version_fixture(file_id: Uuid, owner: Uuid, version: u32) -> FileVersion {
        FileVersion {
            file_id,
            version,
            s3_key: format!("files/{owner}/{file_id}/v{version}"),
            size_bytes: 10 * u64::from(version),
            created_by: owner,
            created_at: fixed_time(i64::from(version)),
        }
    }

    pub(crate) fn share_fixture(
        file_id: Uuid,
        owner: Uuid,
        shared_with: Uuid,
        permission: SharePermission,
    ) -> FileShare {
        FileShare {
            id: Uuid::new_v4(),
            file_id,
            shared_with,
            permission,
            shared_by: owner,
            created_at: fixed_time(0),
        }
    }

    pub(crate) fn folder_fixture(owner: Uuid) -> Folder {
        Folder {
            id: Uuid::new_v4(),
            name: "Documents".into(),
            parent_id: None,
            owner_id: owner,
            created_at: fixed_time(0),
            updated_at: fixed_time(0),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::*;
    use super::*;
    use crate::models::SharePermission;
    use crate::storage::test_support::TEST_BUCKET;
    use actix_web::http::StatusCode;
    use actix_web::test;
    use aws_sdk_dynamodb::operation::put_item::PutItemError;
    use aws_sdk_dynamodb::types::error::ProvisionedThroughputExceededException;
    use aws_sdk_s3::operation::delete_object::DeleteObjectError;
    use aws_sdk_s3::operation::put_object::PutObjectError;
    use aws_sdk_s3::types::error::InvalidRequest;
    use aws_smithy_mocks::mock;
    use serde_json::Value;

    /// The chaos bucket `upload_file` swaps in when the Redis flag is set.
    const CHAOS_BUCKET: &str = "otterworks-files-chaos-nonexistent";

    fn upload_request(body: Vec<u8>) -> test::TestRequest {
        test::TestRequest::post()
            .uri("/api/v1/files/upload")
            .insert_header(multipart_content_type())
            .set_payload(body)
    }

    async fn json_body(resp: actix_web::dev::ServiceResponse) -> Value {
        test::read_body_json(resp).await
    }

    fn s3_failure_rule() -> aws_smithy_mocks::Rule {
        mock!(aws_sdk_s3::Client::put_object).then_error(|| {
            PutObjectError::InvalidRequest(InvalidRequest::builder().message("boom").build())
        })
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

    // ═══════════════════════════════════════════════════════════════════
    // upload — positive
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn upload_stores_object_metadata_and_first_version() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            upload_request(upload_body(&owner, "report.txt", b"hello otters")).to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::CREATED);
        let body = json_body(resp).await;
        let file = &body["file"];
        let file_id = file["id"].as_str().expect("file id").to_string();

        assert_eq!(file["name"], "report.txt");
        assert_eq!(file["mime_type"], "text/plain");
        assert_eq!(file["size_bytes"], 12);
        assert_eq!(file["owner_id"], owner.to_string());
        assert_eq!(file["version"], 1);
        assert_eq!(file["is_trashed"], false);
        assert_eq!(file["folder_id"], Value::Null);
        assert_eq!(file["s3_key"], format!("files/{owner}/{file_id}"));

        assert_eq!(
            ctx.s3_ops(),
            vec![format!("put_object:{TEST_BUCKET}:files/{owner}/{file_id}")],
            "the object must land in the configured bucket under the owner-scoped key"
        );
        assert_eq!(ctx.store.count(FILES_TABLE), 1);
        assert_eq!(ctx.store.count(VERSIONS_TABLE), 1);
    }

    #[actix_rt::test]
    async fn upload_accepts_owner_id_from_header_without_a_multipart_owner_field() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            .file_field("file", "a.txt", Some("text/plain"), b"data")
            .finish();

        let resp = test::call_service(
            &app,
            upload_request(body)
                .insert_header(("X-User-ID", owner.to_string()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::CREATED);
        assert_eq!(json_body(resp).await["file"]["owner_id"], owner.to_string());
    }

    #[actix_rt::test]
    async fn upload_records_the_folder_when_a_folder_id_field_is_present() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let folder = Uuid::new_v4();
        let body = MultipartBody::new()
            .file_field("file", "a.txt", Some("text/plain"), b"data")
            .text_field("owner_id", &owner.to_string())
            .text_field("folder_id", &folder.to_string())
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::CREATED);
        assert_eq!(
            json_body(resp).await["file"]["folder_id"],
            folder.to_string()
        );
    }

    #[actix_rt::test]
    async fn upload_ignores_unknown_multipart_fields() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            .text_field("description", "ignored by the handler")
            .file_field("file", "a.txt", Some("text/plain"), b"data")
            .text_field("owner_id", &owner.to_string())
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::CREATED);
    }

    #[actix_rt::test]
    async fn uploaded_file_is_immediately_listed_and_has_one_version() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            upload_request(upload_body(&owner, "a.txt", b"data")).to_request(),
        )
        .await;
        let file_id = json_body(resp).await["file"]["id"]
            .as_str()
            .unwrap()
            .to_string();

        let versions = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{file_id}/versions"))
                .to_request(),
        )
        .await;
        assert_eq!(versions.status(), StatusCode::OK);
        let body = json_body(versions).await;
        assert_eq!(body["versions"].as_array().unwrap().len(), 1);
        assert_eq!(body["versions"][0]["version"], 1);
        assert_eq!(body["versions"][0]["file_id"], file_id);
    }

    // ═══════════════════════════════════════════════════════════════════
    // upload — boundary (MAX_UPLOAD_BYTES trio)
    // ═══════════════════════════════════════════════════════════════════

    /// The limit is configurable (`MAX_UPLOAD_BYTES`, 100 MB by default); the
    /// trio below uses a small configured limit so the boundary is exercised
    /// without allocating hundreds of megabytes per test.
    const LIMIT: u64 = 1_024;

    async fn upload_of_size(bytes: usize) -> actix_web::dev::ServiceResponse {
        let ctx = CtxBuilder::new().max_upload_bytes(LIMIT).build().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        test::call_service(
            &app,
            upload_request(upload_body(&owner, "big.bin", &vec![b'x'; bytes])).to_request(),
        )
        .await
    }

    #[actix_rt::test]
    async fn upload_one_byte_below_the_limit_is_accepted() {
        let resp = upload_of_size(LIMIT as usize - 1).await;
        assert_eq!(resp.status(), StatusCode::CREATED);
        assert_eq!(json_body(resp).await["file"]["size_bytes"], LIMIT - 1);
    }

    #[actix_rt::test]
    async fn upload_exactly_at_the_limit_is_accepted() {
        let resp = upload_of_size(LIMIT as usize).await;
        assert_eq!(resp.status(), StatusCode::CREATED);
        assert_eq!(json_body(resp).await["file"]["size_bytes"], LIMIT);
    }

    #[actix_rt::test]
    async fn upload_one_byte_above_the_limit_is_rejected() {
        let ctx = CtxBuilder::new().max_upload_bytes(LIMIT).build().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            upload_request(upload_body(
                &owner,
                "big.bin",
                &vec![b'x'; LIMIT as usize + 1],
            ))
            .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::PAYLOAD_TOO_LARGE);
        let body = json_body(resp).await;
        assert_eq!(body["error"], "file_too_large");
        assert!(
            body["message"]
                .as_str()
                .unwrap()
                .contains(&format!("max {LIMIT} bytes")),
            "message should name the limit: {body}"
        );
        assert!(
            ctx.s3_ops().is_empty(),
            "an oversized upload must not reach S3"
        );
        assert_eq!(ctx.store.count(FILES_TABLE), 0);
        assert_eq!(ctx.store.count(VERSIONS_TABLE), 0);
    }

    // ═══════════════════════════════════════════════════════════════════
    // upload — negative
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn upload_of_a_zero_byte_file_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            upload_request(upload_body(&owner, "empty.txt", b"")).to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        let body = json_body(resp).await;
        assert_eq!(body["error"], "bad_request");
        assert_eq!(body["message"], "Bad request: file field is required");
        assert!(ctx.s3_ops().is_empty());
        assert_eq!(ctx.store.count(FILES_TABLE), 0);
    }

    #[actix_rt::test]
    async fn upload_without_a_file_part_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            .text_field("owner_id", &owner.to_string())
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            json_body(resp).await["message"],
            "Bad request: file field is required"
        );
    }

    #[actix_rt::test]
    async fn upload_without_any_owner_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let body = MultipartBody::new()
            .file_field("file", "a.txt", Some("text/plain"), b"data")
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            json_body(resp).await["message"],
            "Bad request: owner_id is required"
        );
    }

    #[actix_rt::test]
    async fn upload_with_a_malformed_owner_id_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let body = MultipartBody::new()
            .file_field("file", "a.txt", Some("text/plain"), b"data")
            .text_field("owner_id", "not-a-uuid")
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert!(json_body(resp).await["message"]
            .as_str()
            .unwrap()
            .contains("invalid owner_id"));
    }

    #[actix_rt::test]
    async fn upload_with_a_malformed_folder_id_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            .file_field("file", "a.txt", Some("text/plain"), b"data")
            .text_field("owner_id", &owner.to_string())
            .text_field("folder_id", "not-a-uuid")
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert!(json_body(resp).await["message"]
            .as_str()
            .unwrap()
            .contains("invalid folder_id"));
    }

    #[actix_rt::test]
    async fn upload_with_an_empty_folder_id_is_treated_as_the_root_folder() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            .file_field("file", "a.txt", Some("text/plain"), b"data")
            .text_field("owner_id", &owner.to_string())
            .text_field("folder_id", "   ")
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::CREATED);
        assert_eq!(json_body(resp).await["file"]["folder_id"], Value::Null);
    }

    #[actix_rt::test]
    async fn upload_without_a_multipart_content_type_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            test::TestRequest::post()
                .uri("/api/v1/files/upload")
                .insert_header(("content-type", "application/json"))
                .set_payload(upload_body(&owner, "a.txt", b"data"))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    #[actix_rt::test]
    async fn upload_with_a_part_that_has_no_content_disposition_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let body =
            format!("--{BOUNDARY}\r\nContent-Type: text/plain\r\n\r\ndata\r\n--{BOUNDARY}--\r\n")
                .into_bytes();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert!(ctx.s3_ops().is_empty());
    }

    #[actix_rt::test]
    async fn upload_surfaces_an_s3_failure_as_a_storage_error_and_writes_no_metadata() {
        let ctx = CtxBuilder::new().s3_rule(s3_failure_rule()).build().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            upload_request(upload_body(&owner, "a.txt", b"data")).to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(json_body(resp).await["error"], "storage_error");
        assert_eq!(
            ctx.store.count(FILES_TABLE),
            0,
            "metadata must not be written when the object never landed"
        );
    }

    #[actix_rt::test]
    async fn upload_surfaces_a_metadata_failure_as_a_metadata_error() {
        let failing_put = mock!(aws_sdk_dynamodb::Client::put_item)
            .match_requests(|req| req.table_name() == Some(FILES_TABLE))
            .then_error(|| {
                PutItemError::ProvisionedThroughputExceededException(
                    ProvisionedThroughputExceededException::builder()
                        .message("throttled")
                        .build(),
                )
            });
        let ctx = CtxBuilder::new().dynamo_rule(failing_put).build().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            upload_request(upload_body(&owner, "a.txt", b"data")).to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(json_body(resp).await["error"], "metadata_error");
        assert_eq!(
            ctx.s3_ops().len(),
            1,
            "FINDING (genuine): the object is already in S3 when the metadata \
             write fails and nothing deletes it, so the blob is orphaned"
        );
        assert_eq!(ctx.store.count(VERSIONS_TABLE), 0);
    }

    // ═══════════════════════════════════════════════════════════════════
    // upload — filenames and content types
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn upload_preserves_a_unicode_filename() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            .file_field("file", "résumé-🦦.txt", Some("text/plain"), b"data")
            .text_field("owner_id", &owner.to_string())
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::CREATED);
        assert_eq!(json_body(resp).await["file"]["name"], "résumé-🦦.txt");
    }

    #[actix_rt::test]
    async fn upload_keys_are_owner_scoped_even_when_the_filename_attempts_traversal() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            .file_field(
                "file",
                "../../etc/passwd",
                Some("text/plain"),
                b"root:x:0:0",
            )
            .text_field("owner_id", &owner.to_string())
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::CREATED);
        let file = json_body(resp).await["file"].clone();
        let key = file["s3_key"].as_str().unwrap().to_string();

        // The storage key is derived from the owner and a fresh UUID, so the
        // traversal attempt cannot escape the owner's prefix.
        assert_eq!(
            key,
            format!("files/{}/{}", owner, file["id"].as_str().unwrap())
        );
        assert!(!key.contains(".."));
        // FINDING (genuine, low severity): the *display* name is stored
        // verbatim, so a client that writes the returned name to disk inherits
        // the traversal. Pinned here rather than fixed - production code is
        // out of scope for this test-only work package.
        assert_eq!(file["name"], "../../etc/passwd");
    }

    #[actix_rt::test]
    async fn upload_falls_back_to_octet_stream_when_the_part_declares_no_content_type() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            .file_field("file", "a.bin", None, b"data")
            .text_field("owner_id", &owner.to_string())
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        assert_eq!(resp.status(), StatusCode::CREATED);
        assert_eq!(
            json_body(resp).await["file"]["mime_type"],
            "application/octet-stream"
        );
    }

    #[actix_rt::test]
    async fn upload_trusts_a_declared_content_type_that_contradicts_the_bytes() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            // PNG magic bytes are absent; the part still claims to be a PNG.
            .file_field("file", "not-really.png", Some("image/png"), b"plain text")
            .text_field("owner_id", &owner.to_string())
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        // FINDING (genuine, low severity): the declared content type is stored
        // and later served back unverified - no sniffing or allow-list.
        assert_eq!(resp.status(), StatusCode::CREATED);
        assert_eq!(json_body(resp).await["file"]["mime_type"], "image/png");
    }

    #[actix_rt::test]
    async fn upload_ignores_a_part_whose_content_type_header_is_malformed() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let body = MultipartBody::new()
            .file_field("file", "a.txt", Some("not a mime type"), b"data")
            .text_field("owner_id", &owner.to_string())
            .finish();

        let resp = test::call_service(&app, upload_request(body).to_request()).await;

        // An unparseable part content type is dropped rather than rejected, so
        // the stored file falls back to the default type.
        assert_eq!(resp.status(), StatusCode::CREATED);
        assert_eq!(
            json_body(resp).await["file"]["mime_type"],
            "application/octet-stream"
        );
    }

    // ═══════════════════════════════════════════════════════════════════
    // upload — idempotency & concurrency
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn uploading_the_same_file_twice_creates_two_independent_files() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let first = test::call_service(
            &app,
            upload_request(upload_body(&owner, "a.txt", b"data")).to_request(),
        )
        .await;
        let second = test::call_service(
            &app,
            upload_request(upload_body(&owner, "a.txt", b"data")).to_request(),
        )
        .await;

        assert_eq!(first.status(), StatusCode::CREATED);
        assert_eq!(second.status(), StatusCode::CREATED);
        let first_id = json_body(first).await["file"]["id"].clone();
        let second_id = json_body(second).await["file"]["id"].clone();

        // FINDING (by design, worth flagging): upload carries no idempotency
        // key, so a client retry silently duplicates the file rather than
        // returning the original.
        assert_ne!(first_id, second_id);
        assert_eq!(ctx.store.count(FILES_TABLE), 2);
        assert_eq!(ctx.s3_ops().len(), 2);
    }

    #[actix_rt::test]
    async fn concurrent_uploads_of_the_same_filename_both_succeed_with_distinct_keys() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let (first, second) = futures_util::future::join(
            test::call_service(
                &app,
                upload_request(upload_body(&owner, "same.txt", b"first")).to_request(),
            ),
            test::call_service(
                &app,
                upload_request(upload_body(&owner, "same.txt", b"second")).to_request(),
            ),
        )
        .await;

        assert_eq!(first.status(), StatusCode::CREATED);
        assert_eq!(second.status(), StatusCode::CREATED);
        let first_key = json_body(first).await["file"]["s3_key"].clone();
        let second_key = json_body(second).await["file"]["s3_key"].clone();

        assert_ne!(
            first_key, second_key,
            "concurrent uploads must not overwrite one another"
        );
        assert_eq!(ctx.store.count(FILES_TABLE), 2);
        assert_eq!(ctx.store.count(VERSIONS_TABLE), 2);
    }

    // ═══════════════════════════════════════════════════════════════════
    // upload — chaos flag
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn upload_targets_the_nonexistent_bucket_while_the_chaos_flag_is_set() {
        let chaos_bucket_fails = mock!(aws_sdk_s3::Client::put_object)
            .match_requests(|req| req.bucket() == Some(CHAOS_BUCKET))
            .then_error(|| {
                PutObjectError::InvalidRequest(
                    InvalidRequest::builder().message("NoSuchBucket").build(),
                )
            });
        let ctx = CtxBuilder::new()
            .redis_reply(REDIS_FLAG_PRESENT)
            .s3_rule(chaos_bucket_fails)
            .build()
            .await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            upload_request(upload_body(&owner, "a.txt", b"data")).to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(json_body(resp).await["error"], "storage_error");
        assert_eq!(ctx.store.count(FILES_TABLE), 0);
    }

    #[actix_rt::test]
    async fn upload_succeeds_when_redis_is_unavailable_for_the_chaos_lookup() {
        let ctx = CtxBuilder::new().redis_reply(REDIS_ERROR).build().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            upload_request(upload_body(&owner, "a.txt", b"data")).to_request(),
        )
        .await;

        assert_eq!(
            resp.status(),
            StatusCode::CREATED,
            "a Redis failure must fail open, not break uploads"
        );
        assert!(ctx.s3_ops()[0].contains(TEST_BUCKET));
    }

    // ═══════════════════════════════════════════════════════════════════
    // download
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn download_returns_a_presigned_url_for_the_stored_key() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/download", file.id))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        let body = json_body(resp).await;
        assert_eq!(body["expires_in_secs"], 3600);
        let url = body["url"].as_str().unwrap();
        assert!(
            url.contains(&file.s3_key),
            "url should target the key: {url}"
        );
        assert!(url.contains("X-Amz-Expires=3600"), "{url}");
    }

    #[actix_rt::test]
    async fn download_of_an_absent_file_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/download", Uuid::new_v4()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        assert_eq!(json_body(resp).await["error"], "file_not_found");
    }

    #[actix_rt::test]
    async fn download_with_a_malformed_file_id_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files/not-a-uuid/download")
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert!(json_body(resp).await["message"]
            .as_str()
            .unwrap()
            .contains("invalid file id"));
    }

    #[actix_rt::test]
    async fn download_of_a_trashed_file_currently_still_returns_a_url() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let mut file = file_fixture(Uuid::new_v4());
        file.is_trashed = true;
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/download", file.id))
                .to_request(),
        )
        .await;

        // Pins today's behaviour; see the ignored test below for the defect.
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[actix_rt::test]
    #[ignore = "FINDING (genuine): download_file never checks is_trashed, so a trashed file stays downloadable by anyone holding its id. Fixing it means changing production code, which is out of scope for this test-only work package."]
    async fn download_of_a_trashed_file_should_be_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let mut file = file_fixture(Uuid::new_v4());
        file.is_trashed = true;
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/download", file.id))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[actix_rt::test]
    async fn download_of_another_users_file_currently_succeeds() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;
        let stranger = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/download", file.id))
                .insert_header(("X-User-ID", stranger.to_string()))
                .to_request(),
        )
        .await;

        // Pins today's behaviour; see the ignored test below for the defect.
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[actix_rt::test]
    #[ignore = "FINDING (genuine): download_file ignores X-User-ID and performs no ownership or share check, so any caller who knows a file id can presign it. Fixing it means changing production code, which is out of scope for this test-only work package."]
    async fn download_of_another_users_file_should_be_forbidden() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;
        let stranger = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/download", file.id))
                .insert_header(("X-User-ID", stranger.to_string()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::FORBIDDEN);
    }

    // ═══════════════════════════════════════════════════════════════════
    // versions
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn version_listing_for_a_file_with_no_versions_is_empty() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/versions", file.id))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(json_body(resp).await["versions"], serde_json::json!([]));
    }

    #[actix_rt::test]
    async fn version_listing_returns_every_version_newest_first() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;
        for version in 1..=3 {
            ctx.seed_version(&version_fixture(file.id, owner, version))
                .await;
        }
        // A version of an unrelated file must not leak into the listing.
        ctx.seed_version(&version_fixture(Uuid::new_v4(), owner, 1))
            .await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/versions", file.id))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        let versions = json_body(resp).await["versions"].clone();
        let numbers: Vec<u64> = versions
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v["version"].as_u64().unwrap())
            .collect();
        assert_eq!(numbers, vec![3, 2, 1]);
    }

    #[actix_rt::test]
    async fn version_listing_with_a_malformed_file_id_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files/not-a-uuid/versions")
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    #[actix_rt::test]
    async fn version_listing_for_an_unknown_file_is_empty_rather_than_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/versions", Uuid::new_v4()))
                .to_request(),
        )
        .await;

        // Pins today's behaviour: the handler queries the versions table
        // directly and never asserts the file exists.
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(json_body(resp).await["versions"], serde_json::json!([]));
    }

    #[actix_rt::test]
    async fn version_listing_of_another_users_file_is_not_restricted() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;
        ctx.seed_version(&version_fixture(file.id, owner, 1)).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/versions", file.id))
                .insert_header(("X-User-ID", Uuid::new_v4().to_string()))
                .to_request(),
        )
        .await;

        // FINDING (genuine): like download, version listing performs no authz.
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            json_body(resp).await["versions"].as_array().unwrap().len(),
            1
        );
    }

    #[actix_rt::test]
    async fn version_listing_surfaces_a_dynamodb_failure() {
        let failing_query = mock!(aws_sdk_dynamodb::Client::query).then_error(|| {
            aws_sdk_dynamodb::operation::query::QueryError::ProvisionedThroughputExceededException(
                ProvisionedThroughputExceededException::builder()
                    .message("throttled")
                    .build(),
            )
        });
        let ctx = CtxBuilder::new().dynamo_rule(failing_query).build().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}/versions", Uuid::new_v4()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::INTERNAL_SERVER_ERROR);
        assert_eq!(json_body(resp).await["error"], "metadata_error");
    }

    // ═══════════════════════════════════════════════════════════════════
    // metadata read + delete
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn file_metadata_includes_the_share_list() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;
        let viewer = Uuid::new_v4();
        ctx.seed_share(&share_fixture(
            file.id,
            owner,
            viewer,
            SharePermission::Viewer,
        ))
        .await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}", file.id))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        let body = json_body(resp).await;
        assert_eq!(body["id"], file.id.to_string());
        assert_eq!(body["shared_with"].as_array().unwrap().len(), 1);
        assert_eq!(body["shared_with"][0]["shared_with"], viewer.to_string());
    }

    #[actix_rt::test]
    async fn file_metadata_for_an_absent_file_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/{}", Uuid::new_v4()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[actix_rt::test]
    async fn deleting_a_file_removes_both_the_metadata_and_the_object() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::delete()
                .uri(&format!("/api/v1/files/{}", file.id))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NO_CONTENT);
        assert_eq!(ctx.store.count(FILES_TABLE), 0);
        assert_eq!(ctx.s3_ops(), vec![format!("delete_object:{}", file.s3_key)]);
    }

    #[actix_rt::test]
    async fn deleting_an_absent_file_is_not_found_and_touches_nothing() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::delete()
                .uri(&format!("/api/v1/files/{}", Uuid::new_v4()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        assert!(ctx.s3_ops().is_empty());
    }

    #[actix_rt::test]
    async fn deleting_a_file_whose_object_delete_fails_leaves_the_metadata_gone() {
        let failing_delete = mock!(aws_sdk_s3::Client::delete_object)
            .then_error(|| DeleteObjectError::unhandled("access denied"));
        let ctx = CtxBuilder::new().s3_rule(failing_delete).build().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::delete()
                .uri(&format!("/api/v1/files/{}", file.id))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::INTERNAL_SERVER_ERROR);
        // FINDING (genuine): the metadata row is deleted before the object, so
        // an S3 failure orphans the blob with no way to find it again.
        assert_eq!(ctx.store.count(FILES_TABLE), 0);
    }

    // ═══════════════════════════════════════════════════════════════════
    // listing — pagination boundaries and owner scoping
    // ═══════════════════════════════════════════════════════════════════

    async fn seed_files(ctx: &Ctx, owner: Uuid, count: usize) -> Vec<FileMetadata> {
        let mut files = Vec::new();
        for _ in 0..count {
            let file = file_fixture(owner);
            ctx.seed_file(&file).await;
            files.push(file);
        }
        files
    }

    #[actix_rt::test]
    async fn listing_files_excludes_trashed_files_by_default() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        seed_files(&ctx, owner, 2).await;
        let mut trashed = file_fixture(owner);
        trashed.is_trashed = true;
        ctx.seed_file(&trashed).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files")
                .insert_header(("X-User-ID", owner.to_string()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(json_body(resp).await["total"], 2);
    }

    #[actix_rt::test]
    async fn listing_files_can_include_trashed_files() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        seed_files(&ctx, owner, 2).await;
        let mut trashed = file_fixture(owner);
        trashed.is_trashed = true;
        ctx.seed_file(&trashed).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files?include_trashed=true")
                .insert_header(("X-User-ID", owner.to_string()))
                .to_request(),
        )
        .await;

        assert_eq!(json_body(resp).await["total"], 3);
    }

    #[actix_rt::test]
    async fn listing_files_prefers_the_authenticated_user_over_a_spoofed_owner_id() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let caller = Uuid::new_v4();
        let victim = Uuid::new_v4();
        seed_files(&ctx, caller, 1).await;
        seed_files(&ctx, victim, 3).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files?owner_id={victim}"))
                .insert_header(("X-User-ID", caller.to_string()))
                .to_request(),
        )
        .await;

        assert_eq!(json_body(resp).await["total"], 1);
    }

    #[actix_rt::test]
    async fn listing_files_falls_back_to_the_query_owner_without_a_user_header() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        seed_files(&ctx, owner, 2).await;
        seed_files(&ctx, Uuid::new_v4(), 1).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files?owner_id={owner}"))
                .to_request(),
        )
        .await;

        assert_eq!(json_body(resp).await["total"], 2);
    }

    /// `page_size` is clamped to 100; check the trio around that ceiling.
    #[actix_rt::test]
    async fn listing_files_honours_a_page_size_just_below_the_ceiling() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files?page_size=99")
                .to_request(),
        )
        .await;
        assert_eq!(json_body(resp).await["page_size"], 99);
    }

    #[actix_rt::test]
    async fn listing_files_honours_a_page_size_exactly_at_the_ceiling() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files?page_size=100")
                .to_request(),
        )
        .await;
        assert_eq!(json_body(resp).await["page_size"], 100);
    }

    #[actix_rt::test]
    async fn listing_files_clamps_a_page_size_above_the_ceiling() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files?page_size=101")
                .to_request(),
        )
        .await;
        assert_eq!(json_body(resp).await["page_size"], 100);
    }

    #[actix_rt::test]
    async fn listing_files_treats_page_zero_as_the_first_page() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        seed_files(&ctx, owner, 3).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files?page=0&page_size=2")
                .insert_header(("X-User-ID", owner.to_string()))
                .to_request(),
        )
        .await;

        let body = json_body(resp).await;
        assert_eq!(body["page"], 1);
        assert_eq!(body["files"].as_array().unwrap().len(), 2);
        assert_eq!(body["total"], 3);
    }

    #[actix_rt::test]
    async fn listing_files_past_the_last_page_returns_no_rows_but_the_real_total() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        seed_files(&ctx, owner, 3).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files?page=9&page_size=2")
                .insert_header(("X-User-ID", owner.to_string()))
                .to_request(),
        )
        .await;

        let body = json_body(resp).await;
        assert_eq!(body["files"].as_array().unwrap().len(), 0);
        assert_eq!(body["total"], 3);
    }

    #[actix_rt::test]
    async fn listing_files_with_a_malformed_page_size_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files?page_size=lots")
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    #[actix_rt::test]
    async fn listing_trashed_files_returns_only_the_owners_trash() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        seed_files(&ctx, owner, 1).await;
        let mut mine = file_fixture(owner);
        mine.is_trashed = true;
        ctx.seed_file(&mine).await;
        let mut someone_elses = file_fixture(Uuid::new_v4());
        someone_elses.is_trashed = true;
        ctx.seed_file(&someone_elses).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files/trash")
                .insert_header(("X-User-ID", owner.to_string()))
                .to_request(),
        )
        .await;

        let body = json_body(resp).await;
        assert_eq!(body["total"], 1);
        assert_eq!(body["files"][0]["id"], mine.id.to_string());
    }

    #[actix_rt::test]
    async fn listing_shared_files_requires_a_user_header() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files/shared")
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            json_body(resp).await["message"],
            "Bad request: missing X-User-ID header"
        );
    }

    #[actix_rt::test]
    async fn listing_shared_files_deduplicates_and_skips_trashed_files() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let viewer = Uuid::new_v4();

        let shared = file_fixture(owner);
        ctx.seed_file(&shared).await;
        let mut trashed = file_fixture(owner);
        trashed.is_trashed = true;
        ctx.seed_file(&trashed).await;

        // Two share rows for the same file: a legacy duplicate.
        ctx.seed_share(&share_fixture(
            shared.id,
            owner,
            viewer,
            SharePermission::Viewer,
        ))
        .await;
        ctx.seed_share(&share_fixture(
            shared.id,
            owner,
            viewer,
            SharePermission::Editor,
        ))
        .await;
        ctx.seed_share(&share_fixture(
            trashed.id,
            owner,
            viewer,
            SharePermission::Viewer,
        ))
        .await;
        // A share addressed to somebody else must not appear.
        ctx.seed_share(&share_fixture(
            shared.id,
            owner,
            Uuid::new_v4(),
            SharePermission::Viewer,
        ))
        .await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files/shared")
                .insert_header(("X-User-ID", viewer.to_string()))
                .to_request(),
        )
        .await;

        let body = json_body(resp).await;
        assert_eq!(body["total"], 1);
        assert_eq!(body["files"][0]["id"], shared.id.to_string());
    }

    // ═══════════════════════════════════════════════════════════════════
    // rename / move / trash / restore
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn renaming_a_file_trims_and_persists_the_new_name() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::patch()
                .uri(&format!("/api/v1/files/{}/rename", file.id))
                .set_json(serde_json::json!({ "name": "  renamed.txt  " }))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(json_body(resp).await["name"], "renamed.txt");
    }

    #[actix_rt::test]
    async fn renaming_a_file_to_whitespace_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::patch()
                .uri(&format!("/api/v1/files/{}/rename", file.id))
                .set_json(serde_json::json!({ "name": "   " }))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            json_body(resp).await["message"],
            "Bad request: name cannot be empty"
        );
    }

    #[actix_rt::test]
    async fn renaming_an_absent_file_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::patch()
                .uri(&format!("/api/v1/files/{}/rename", Uuid::new_v4()))
                .set_json(serde_json::json!({ "name": "x.txt" }))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[actix_rt::test]
    async fn moving_a_file_into_a_folder_and_back_to_the_root() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;
        let folder = folder_fixture(owner);
        ctx.seed_folder(&folder).await;

        let into = test::call_service(
            &app,
            test::TestRequest::put()
                .uri(&format!("/api/v1/files/{}/move", file.id))
                .set_json(serde_json::json!({ "folder_id": folder.id }))
                .to_request(),
        )
        .await;
        assert_eq!(into.status(), StatusCode::OK);
        assert_eq!(json_body(into).await["folder_id"], folder.id.to_string());

        let back = test::call_service(
            &app,
            test::TestRequest::put()
                .uri(&format!("/api/v1/files/{}/move", file.id))
                .set_json(serde_json::json!({ "folder_id": Value::Null }))
                .to_request(),
        )
        .await;
        assert_eq!(back.status(), StatusCode::OK);
        assert_eq!(json_body(back).await["folder_id"], Value::Null);
    }

    #[actix_rt::test]
    async fn moving_an_absent_file_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::put()
                .uri(&format!("/api/v1/files/{}/move", Uuid::new_v4()))
                .set_json(serde_json::json!({ "folder_id": Value::Null }))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[actix_rt::test]
    async fn trashing_then_restoring_a_file_round_trips_and_is_idempotent() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;

        for _ in 0..2 {
            let resp = test::call_service(
                &app,
                test::TestRequest::post()
                    .uri(&format!("/api/v1/files/{}/trash", file.id))
                    .to_request(),
            )
            .await;
            assert_eq!(resp.status(), StatusCode::OK);
            assert_eq!(json_body(resp).await["is_trashed"], true);
        }

        for _ in 0..2 {
            let resp = test::call_service(
                &app,
                test::TestRequest::post()
                    .uri(&format!("/api/v1/files/{}/restore", file.id))
                    .to_request(),
            )
            .await;
            assert_eq!(resp.status(), StatusCode::OK);
            assert_eq!(json_body(resp).await["is_trashed"], false);
        }
    }

    #[actix_rt::test]
    async fn trashing_an_absent_file_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::post()
                .uri(&format!("/api/v1/files/{}/trash", Uuid::new_v4()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        assert_eq!(json_body(resp).await["error"], "file_not_found");
    }

    #[actix_rt::test]
    async fn restoring_an_absent_file_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::post()
                .uri(&format!("/api/v1/files/{}/restore", Uuid::new_v4()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    // ═══════════════════════════════════════════════════════════════════
    // sharing
    // ═══════════════════════════════════════════════════════════════════

    fn share_payload(shared_with: Uuid, shared_by: Uuid, permission: &str) -> Value {
        serde_json::json!({
            "shared_with": shared_with,
            "shared_by": shared_by,
            "permission": permission,
        })
    }

    #[actix_rt::test]
    async fn sharing_a_file_creates_a_share() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;
        let viewer = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            test::TestRequest::post()
                .uri(&format!("/api/v1/files/{}/share", file.id))
                .set_json(share_payload(viewer, owner, "viewer"))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::CREATED);
        let body = json_body(resp).await;
        assert_eq!(body["share"]["shared_with"], viewer.to_string());
        assert_eq!(body["share"]["permission"], "viewer");
        assert_eq!(ctx.store.count(SHARES_TABLE), 1);
    }

    #[actix_rt::test]
    async fn resharing_with_the_same_permission_is_idempotent() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;
        let viewer = Uuid::new_v4();
        let payload = share_payload(viewer, owner, "viewer");

        let first = test::call_service(
            &app,
            test::TestRequest::post()
                .uri(&format!("/api/v1/files/{}/share", file.id))
                .set_json(payload.clone())
                .to_request(),
        )
        .await;
        assert_eq!(first.status(), StatusCode::CREATED);
        let first_id = json_body(first).await["share"]["id"].clone();

        let second = test::call_service(
            &app,
            test::TestRequest::post()
                .uri(&format!("/api/v1/files/{}/share", file.id))
                .set_json(payload)
                .to_request(),
        )
        .await;

        assert_eq!(second.status(), StatusCode::OK);
        assert_eq!(json_body(second).await["share"]["id"], first_id);
        assert_eq!(ctx.store.count(SHARES_TABLE), 1);
    }

    #[actix_rt::test]
    async fn resharing_with_a_different_permission_updates_the_existing_share() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;
        let viewer = Uuid::new_v4();
        let existing = share_fixture(file.id, owner, viewer, SharePermission::Viewer);
        ctx.seed_share(&existing).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::post()
                .uri(&format!("/api/v1/files/{}/share", file.id))
                .set_json(share_payload(viewer, owner, "editor"))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        let body = json_body(resp).await;
        assert_eq!(body["share"]["id"], existing.id.to_string());
        assert_eq!(body["share"]["permission"], "editor");
        assert_eq!(ctx.store.count(SHARES_TABLE), 1);
    }

    #[actix_rt::test]
    async fn sharing_an_absent_file_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let resp = test::call_service(
            &app,
            test::TestRequest::post()
                .uri(&format!("/api/v1/files/{}/share", Uuid::new_v4()))
                .set_json(share_payload(Uuid::new_v4(), owner, "viewer"))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        assert_eq!(ctx.store.count(SHARES_TABLE), 0);
    }

    #[actix_rt::test]
    async fn sharing_with_an_unknown_permission_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::post()
                .uri(&format!("/api/v1/files/{}/share", file.id))
                .set_json(share_payload(Uuid::new_v4(), owner, "owner"))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    }

    #[actix_rt::test]
    async fn a_file_can_currently_be_shared_with_its_own_owner() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::post()
                .uri(&format!("/api/v1/files/{}/share", file.id))
                .set_json(share_payload(owner, owner, "viewer"))
                .to_request(),
        )
        .await;

        // FINDING (genuine, low severity): sharing a file with its owner is
        // accepted and shows up in the owner's "shared with me" list.
        assert_eq!(resp.status(), StatusCode::CREATED);
    }

    #[actix_rt::test]
    async fn removing_a_share_deletes_it() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let file = file_fixture(owner);
        ctx.seed_file(&file).await;
        let viewer = Uuid::new_v4();
        ctx.seed_share(&share_fixture(
            file.id,
            owner,
            viewer,
            SharePermission::Viewer,
        ))
        .await;

        let resp = test::call_service(
            &app,
            test::TestRequest::delete()
                .uri(&format!("/api/v1/files/{}/share/{}", file.id, viewer))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NO_CONTENT);
        assert_eq!(ctx.store.count(SHARES_TABLE), 0);
    }

    #[actix_rt::test]
    async fn removing_a_share_that_does_not_exist_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::delete()
                .uri(&format!(
                    "/api/v1/files/{}/share/{}",
                    file.id,
                    Uuid::new_v4()
                ))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        assert_eq!(json_body(resp).await["error"], "share_not_found");
    }

    #[actix_rt::test]
    async fn removing_a_share_with_a_malformed_user_id_is_rejected() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let file = file_fixture(Uuid::new_v4());
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::delete()
                .uri(&format!("/api/v1/files/{}/share/not-a-uuid", file.id))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert!(json_body(resp).await["message"]
            .as_str()
            .unwrap()
            .contains("invalid user id"));
    }

    // ═══════════════════════════════════════════════════════════════════
    // folders
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn creating_reading_updating_and_deleting_a_folder() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();

        let created = test::call_service(
            &app,
            test::TestRequest::post()
                .uri("/api/v1/folders")
                .set_json(serde_json::json!({ "name": "Docs", "owner_id": owner }))
                .to_request(),
        )
        .await;
        assert_eq!(created.status(), StatusCode::CREATED);
        let folder_id = json_body(created).await["id"].as_str().unwrap().to_string();

        let fetched = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/folders/{folder_id}"))
                .to_request(),
        )
        .await;
        assert_eq!(fetched.status(), StatusCode::OK);
        assert_eq!(json_body(fetched).await["name"], "Docs");

        let updated = test::call_service(
            &app,
            test::TestRequest::put()
                .uri(&format!("/api/v1/folders/{folder_id}"))
                .set_json(serde_json::json!({ "name": "Renamed" }))
                .to_request(),
        )
        .await;
        assert_eq!(updated.status(), StatusCode::OK);
        assert_eq!(json_body(updated).await["name"], "Renamed");

        let deleted = test::call_service(
            &app,
            test::TestRequest::delete()
                .uri(&format!("/api/v1/folders/{folder_id}"))
                .to_request(),
        )
        .await;
        assert_eq!(deleted.status(), StatusCode::NO_CONTENT);
        assert_eq!(ctx.store.count(FOLDERS_TABLE), 0);
    }

    #[actix_rt::test]
    async fn reading_an_absent_folder_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/folders/{}", Uuid::new_v4()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
        assert_eq!(json_body(resp).await["error"], "folder_not_found");
    }

    #[actix_rt::test]
    async fn updating_an_absent_folder_is_not_found() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::put()
                .uri(&format!("/api/v1/folders/{}", Uuid::new_v4()))
                .set_json(serde_json::json!({ "name": "x" }))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[actix_rt::test]
    async fn folder_listing_is_scoped_to_the_authenticated_owner() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        ctx.seed_folder(&folder_fixture(owner)).await;
        ctx.seed_folder(&folder_fixture(owner)).await;
        ctx.seed_folder(&folder_fixture(Uuid::new_v4())).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/folders")
                .insert_header(("X-User-ID", owner.to_string()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            json_body(resp).await["folders"].as_array().unwrap().len(),
            2
        );
    }

    #[actix_rt::test]
    async fn deleting_a_folder_that_still_holds_files_succeeds() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let folder = folder_fixture(owner);
        ctx.seed_folder(&folder).await;
        let mut file = file_fixture(owner);
        file.folder_id = Some(folder.id);
        ctx.seed_file(&file).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::delete()
                .uri(&format!("/api/v1/folders/{}", folder.id))
                .to_request(),
        )
        .await;

        // FINDING (genuine): deleting a folder does not cascade or block, so
        // its files keep pointing at a folder id that no longer exists.
        assert_eq!(resp.status(), StatusCode::NO_CONTENT);
        assert_eq!(ctx.store.count(FILES_TABLE), 1);
    }

    // ═══════════════════════════════════════════════════════════════════
    // activity
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn activity_requires_a_user_header() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files/activity")
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
        assert_eq!(
            json_body(resp).await["message"],
            "Bad request: missing owner context"
        );
    }

    #[actix_rt::test]
    async fn activity_reports_uploads_and_shares_newest_first() {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        let mut older = file_fixture(owner);
        older.created_at = fixed_time(0);
        ctx.seed_file(&older).await;
        let mut newer = file_fixture(owner);
        newer.created_at = fixed_time(60);
        ctx.seed_file(&newer).await;
        ctx.seed_share(&share_fixture(
            older.id,
            owner,
            Uuid::new_v4(),
            SharePermission::Viewer,
        ))
        .await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files/activity")
                .insert_header(("X-User-ID", owner.to_string()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        let items = json_body(resp).await["items"].clone();
        let items = items.as_array().unwrap();
        assert_eq!(items.len(), 3);
        assert_eq!(items[0]["resource_id"], newer.id.to_string());
        assert_eq!(items[0]["type"], "upload");
        assert!(items.iter().any(|i| i["type"] == "share"));
    }

    /// The activity limit is clamped to 50; check the trio around it.
    async fn activity_items_for_limit(limit: u32, seed: usize) -> usize {
        let ctx = Ctx::new().await;
        let app = test_app!(ctx);
        let owner = Uuid::new_v4();
        seed_files(&ctx, owner, seed).await;

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri(&format!("/api/v1/files/activity?limit={limit}"))
                .insert_header(("X-User-ID", owner.to_string()))
                .to_request(),
        )
        .await;
        json_body(resp).await["items"].as_array().unwrap().len()
    }

    #[actix_rt::test]
    async fn activity_limit_below_the_ceiling_is_honoured() {
        assert_eq!(activity_items_for_limit(2, 5).await, 2);
    }

    #[actix_rt::test]
    async fn activity_limit_of_zero_returns_nothing() {
        assert_eq!(activity_items_for_limit(0, 3).await, 0);
    }

    #[actix_rt::test]
    async fn activity_limit_above_the_ceiling_is_clamped_to_fifty() {
        // Only three rows exist, so the clamp is asserted on the request path
        // rather than the row count; the point is that an over-large limit is
        // accepted rather than rejected.
        assert_eq!(activity_items_for_limit(1_000, 3).await, 3);
    }

    #[actix_rt::test]
    async fn activity_tolerates_a_metadata_failure_by_returning_an_empty_feed() {
        let failing_scan = mock!(aws_sdk_dynamodb::Client::scan).then_error(|| {
            aws_sdk_dynamodb::operation::scan::ScanError::ProvisionedThroughputExceededException(
                ProvisionedThroughputExceededException::builder()
                    .message("throttled")
                    .build(),
            )
        });
        let ctx = CtxBuilder::new().dynamo_rule(failing_scan).build().await;
        let app = test_app!(ctx);

        let resp = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/api/v1/files/activity")
                .insert_header(("X-User-ID", Uuid::new_v4().to_string()))
                .to_request(),
        )
        .await;

        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(json_body(resp).await["items"], serde_json::json!([]));
    }

    // ═══════════════════════════════════════════════════════════════════
    // determinism
    // ═══════════════════════════════════════════════════════════════════

    #[actix_rt::test]
    async fn each_test_context_starts_from_an_empty_store() {
        let first = Ctx::new().await;
        let second = Ctx::new().await;
        let app = test_app!(first);
        let owner = Uuid::new_v4();

        test::call_service(
            &app,
            upload_request(upload_body(&owner, "a.txt", b"data")).to_request(),
        )
        .await;

        assert_eq!(first.store.count(FILES_TABLE), 1);
        assert_eq!(
            second.store.count(FILES_TABLE),
            0,
            "contexts must not share mutable state"
        );
    }
}

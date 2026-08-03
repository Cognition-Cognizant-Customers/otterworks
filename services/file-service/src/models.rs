use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

// ── File Metadata ──────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileMetadata {
    pub id: Uuid,
    pub name: String,
    pub mime_type: String,
    pub size_bytes: u64,
    pub s3_key: String,
    pub folder_id: Option<Uuid>,
    pub owner_id: Uuid,
    pub version: u32,
    pub is_trashed: bool,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
pub struct FileDetailResponse {
    #[serde(flatten)]
    pub file: FileMetadata,
    pub shared_with: Vec<FileShare>,
}

// ── Folder ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Folder {
    pub id: Uuid,
    pub name: String,
    pub parent_id: Option<Uuid>,
    pub owner_id: Uuid,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

// ── File Version ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileVersion {
    pub file_id: Uuid,
    pub version: u32,
    pub s3_key: String,
    pub size_bytes: u64,
    pub created_by: Uuid,
    pub created_at: DateTime<Utc>,
}

// ── File Share ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileShare {
    pub id: Uuid,
    pub file_id: Uuid,
    pub shared_with: Uuid,
    pub permission: SharePermission,
    pub shared_by: Uuid,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase")]
pub enum SharePermission {
    Viewer,
    Editor,
}

impl std::fmt::Display for SharePermission {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SharePermission::Viewer => write!(f, "viewer"),
            SharePermission::Editor => write!(f, "editor"),
        }
    }
}

impl SharePermission {
    pub fn from_str_value(s: &str) -> Option<Self> {
        match s.to_lowercase().as_str() {
            "viewer" => Some(SharePermission::Viewer),
            "editor" => Some(SharePermission::Editor),
            _ => None,
        }
    }
}

// ── Request / Response Types ───────────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status: String,
    pub service: String,
    pub version: String,
}

#[derive(Debug, Serialize)]
pub struct UploadResponse {
    pub file: FileMetadata,
}

#[derive(Debug, Serialize)]
pub struct DownloadResponse {
    pub url: String,
    pub expires_in_secs: u64,
}

#[derive(Debug, Deserialize)]
pub struct ListFilesQuery {
    pub folder_id: Option<Uuid>,
    pub owner_id: Option<Uuid>,
    pub page: Option<u32>,
    pub page_size: Option<u32>,
    pub include_trashed: Option<bool>,
}

#[derive(Debug, Serialize)]
pub struct ListFilesResponse {
    pub files: Vec<FileMetadata>,
    pub total: usize,
    pub page: u32,
    pub page_size: u32,
}

#[derive(Debug, Serialize)]
pub struct ListVersionsResponse {
    pub versions: Vec<FileVersion>,
}

#[derive(Debug, Deserialize)]
pub struct ListFoldersQuery {
    pub parent_id: Option<Uuid>,
    pub owner_id: Option<Uuid>,
}

#[derive(Debug, Serialize)]
pub struct ListFoldersResponse {
    pub folders: Vec<Folder>,
}

#[derive(Debug, Deserialize)]
pub struct CreateFolderRequest {
    pub name: String,
    pub parent_id: Option<Uuid>,
    pub owner_id: Uuid,
}

#[derive(Debug, Deserialize)]
pub struct UpdateFolderRequest {
    pub name: Option<String>,
    pub parent_id: Option<Uuid>,
}

#[derive(Debug, Deserialize)]
pub struct MoveFileRequest {
    pub folder_id: Option<Uuid>,
}

#[derive(Debug, Deserialize)]
pub struct RenameFileRequest {
    pub name: String,
}

#[derive(Debug, Deserialize)]
pub struct ShareFileRequest {
    pub shared_with: Uuid,
    pub permission: SharePermission,
    pub shared_by: Uuid,
}

#[derive(Debug, Serialize)]
pub struct ShareFileResponse {
    pub share: FileShare,
}

// ── Activity ───────────────────────────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct ActivityItem {
    pub id: String,
    #[serde(rename = "type")]
    pub activity_type: String,
    pub description: String,
    pub actor_name: String,
    pub resource_name: String,
    pub resource_type: String,
    pub resource_id: String,
    pub created_at: String,
}

#[derive(Debug, Deserialize)]
pub struct ActivityQuery {
    pub limit: Option<u32>,
}

#[derive(Debug, Serialize)]
pub struct ActivityResponse {
    pub items: Vec<ActivityItem>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use actix_web::web::Query;
    use chrono::TimeZone;
    use serde_json::{json, Value};

    fn timestamp() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 1, 2, 3, 4, 5).unwrap()
    }

    fn sample_file(folder_id: Option<Uuid>) -> FileMetadata {
        FileMetadata {
            id: Uuid::nil(),
            name: "report.pdf".into(),
            mime_type: "application/pdf".into(),
            size_bytes: 2048,
            s3_key: "files/owner/report".into(),
            folder_id,
            owner_id: Uuid::nil(),
            version: 3,
            is_trashed: false,
            created_at: timestamp(),
            updated_at: timestamp(),
        }
    }

    #[test]
    fn file_metadata_round_trips_through_json() {
        let folder_id = Uuid::new_v4();
        let original = sample_file(Some(folder_id));

        let json = serde_json::to_value(&original).unwrap();
        assert_eq!(json["size_bytes"], 2048);
        assert_eq!(json["folder_id"], folder_id.to_string());
        assert_eq!(json["created_at"], "2026-01-02T03:04:05Z");

        let parsed: FileMetadata = serde_json::from_value(json).unwrap();
        assert_eq!(parsed.id, original.id);
        assert_eq!(parsed.name, original.name);
        assert_eq!(parsed.folder_id, Some(folder_id));
        assert_eq!(parsed.created_at, original.created_at);
    }

    #[test]
    fn a_file_at_the_drive_root_serialises_a_null_folder() {
        let json = serde_json::to_value(sample_file(None)).unwrap();
        assert_eq!(json["folder_id"], Value::Null);
    }

    #[test]
    fn file_detail_response_flattens_the_file_alongside_its_shares() {
        let file = sample_file(None);
        let share = FileShare {
            id: Uuid::new_v4(),
            file_id: file.id,
            shared_with: Uuid::new_v4(),
            permission: SharePermission::Viewer,
            shared_by: Uuid::new_v4(),
            created_at: timestamp(),
        };

        let json = serde_json::to_value(FileDetailResponse {
            file,
            shared_with: vec![share],
        })
        .unwrap();

        // The file fields sit at the top level, not under a "file" key.
        assert_eq!(json["name"], "report.pdf");
        assert!(json.get("file").is_none());
        assert_eq!(json["shared_with"][0]["permission"], "viewer");
    }

    #[test]
    fn share_permissions_serialise_lowercase_and_parse_case_insensitively() {
        assert_eq!(
            serde_json::to_value(SharePermission::Editor).unwrap(),
            json!("editor")
        );
        assert_eq!(
            serde_json::from_value::<SharePermission>(json!("viewer")).unwrap(),
            SharePermission::Viewer
        );
        assert!(serde_json::from_value::<SharePermission>(json!("owner")).is_err());

        assert_eq!(SharePermission::Editor.to_string(), "editor");
        assert_eq!(
            SharePermission::from_str_value("VIEWER"),
            Some(SharePermission::Viewer)
        );
        assert_eq!(SharePermission::from_str_value("owner"), None);
    }

    #[test]
    fn list_files_query_treats_every_field_as_optional() {
        let empty = Query::<ListFilesQuery>::from_query("")
            .unwrap()
            .into_inner();
        assert!(empty.folder_id.is_none());
        assert!(empty.owner_id.is_none());
        assert!(empty.page.is_none());
        assert!(empty.page_size.is_none());
        assert!(empty.include_trashed.is_none());

        let owner = Uuid::new_v4();
        let full = Query::<ListFilesQuery>::from_query(&format!(
            "owner_id={owner}&page=2&page_size=25&include_trashed=true"
        ))
        .unwrap()
        .into_inner();
        assert_eq!(full.owner_id, Some(owner));
        assert_eq!(full.page, Some(2));
        assert_eq!(full.page_size, Some(25));
        assert_eq!(full.include_trashed, Some(true));
    }

    #[test]
    fn a_malformed_uuid_in_a_query_is_rejected() {
        assert!(Query::<ListFilesQuery>::from_query("owner_id=nope").is_err());
    }

    #[test]
    fn create_folder_request_requires_a_name_and_owner() {
        let owner = Uuid::new_v4();
        let parsed: CreateFolderRequest =
            serde_json::from_value(json!({"name": "Q1", "owner_id": owner})).unwrap();
        assert_eq!(parsed.name, "Q1");
        assert_eq!(parsed.owner_id, owner);
        assert!(parsed.parent_id.is_none());

        assert!(serde_json::from_value::<CreateFolderRequest>(json!({"name": "Q1"})).is_err());
    }

    #[test]
    fn share_file_request_rejects_an_unknown_permission() {
        let body = json!({
            "shared_with": Uuid::new_v4(),
            "permission": "admin",
            "shared_by": Uuid::new_v4(),
        });
        assert!(serde_json::from_value::<ShareFileRequest>(body).is_err());
    }

    #[test]
    fn move_file_request_distinguishes_root_from_a_folder() {
        let to_root: MoveFileRequest = serde_json::from_value(json!({"folder_id": null})).unwrap();
        assert!(to_root.folder_id.is_none());

        let folder_id = Uuid::new_v4();
        let to_folder: MoveFileRequest =
            serde_json::from_value(json!({"folder_id": folder_id})).unwrap();
        assert_eq!(to_folder.folder_id, Some(folder_id));
    }

    #[test]
    fn list_responses_carry_their_pagination_envelope() {
        let json = serde_json::to_value(ListFilesResponse {
            files: vec![sample_file(None)],
            total: 1,
            page: 1,
            page_size: 50,
        })
        .unwrap();

        assert_eq!(json["total"], 1);
        assert_eq!(json["page"], 1);
        assert_eq!(json["page_size"], 50);
        assert_eq!(json["files"][0]["name"], "report.pdf");
    }

    #[test]
    fn activity_items_expose_type_as_the_reserved_json_key() {
        let json = serde_json::to_value(ActivityResponse {
            items: vec![ActivityItem {
                id: "upload-1".into(),
                activity_type: "upload".into(),
                description: "Uploaded report.pdf".into(),
                actor_name: "You".into(),
                resource_name: "report.pdf".into(),
                resource_type: "file".into(),
                resource_id: Uuid::nil().to_string(),
                created_at: timestamp().to_rfc3339(),
            }],
        })
        .unwrap();

        assert_eq!(json["items"][0]["type"], "upload");
        assert!(json["items"][0].get("activity_type").is_none());
    }

    #[test]
    fn health_and_download_responses_serialise_their_contract() {
        let health = serde_json::to_value(HealthResponse {
            status: "healthy".into(),
            service: "file-service".into(),
            version: "0.1.0".into(),
        })
        .unwrap();
        assert_eq!(health["status"], "healthy");
        assert_eq!(health["service"], "file-service");

        let download = serde_json::to_value(DownloadResponse {
            url: "https://s3.example/report.pdf?sig".into(),
            expires_in_secs: 3600,
        })
        .unwrap();
        assert_eq!(download["expires_in_secs"], 3600);
        assert_eq!(download["url"], "https://s3.example/report.pdf?sig");
    }
}

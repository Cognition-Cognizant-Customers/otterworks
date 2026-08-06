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

/// WP-02 — wire contract for the share/folder request models.
#[cfg(test)]
mod wire_contract_tests {
    use super::*;
    use serde_json::json;

    fn uuid_a() -> Uuid {
        Uuid::from_u128(0x0100)
    }
    fn uuid_b() -> Uuid {
        Uuid::from_u128(0x0200)
    }

    // -- SharePermission --

    #[test]
    fn permissions_serialise_in_the_lowercase_stored_form() {
        assert_eq!(
            serde_json::to_value(SharePermission::Viewer).unwrap(),
            json!("viewer")
        );
        assert_eq!(
            serde_json::to_value(SharePermission::Editor).unwrap(),
            json!("editor")
        );
    }

    #[test]
    fn permissions_display_exactly_as_they_serialise() {
        for permission in [SharePermission::Viewer, SharePermission::Editor] {
            let serialised = serde_json::to_value(permission.clone()).unwrap();
            assert_eq!(json!(permission.to_string()), serialised);
        }
    }

    /// WP-02 finding F9 (genuine inconsistency, pinned not fixed).
    ///
    /// Two parsers accept two different languages for the same value:
    /// `serde` is case-sensitive, `from_str_value` folds case. `{"permission":
    /// "Viewer"}` is therefore a 400 on the API, while the identical string
    /// read back out of DynamoDB is accepted. Table-driven so the divergence
    /// is explicit per input.
    #[test]
    fn serde_and_from_str_value_disagree_on_casing() {
        let cases: [(&str, bool, bool); 8] = [
            // input, accepted by serde, accepted by from_str_value
            ("viewer", true, true),
            ("editor", true, true),
            ("Viewer", false, true),
            ("VIEWER", false, true),
            ("Editor", false, true),
            ("EDITOR", false, true),
            ("owner", false, false),
            ("", false, false),
        ];

        for (input, serde_accepts, helper_accepts) in cases {
            let via_serde = serde_json::from_value::<SharePermission>(json!(input)).is_ok();
            let via_helper = SharePermission::from_str_value(input).is_some();

            assert_eq!(via_serde, serde_accepts, "serde on {input:?}");
            assert_eq!(via_helper, helper_accepts, "from_str_value on {input:?}");
        }
    }

    #[test]
    fn a_non_string_permission_is_rejected() {
        for value in [json!(1), json!(true), json!(null), json!(["viewer"])] {
            assert!(
                serde_json::from_value::<SharePermission>(value.clone()).is_err(),
                "{value} must not deserialise"
            );
        }
    }

    // -- ShareFileRequest --

    #[test]
    fn a_complete_share_request_parses() {
        let request: ShareFileRequest = serde_json::from_value(json!({
            "shared_with": uuid_b(),
            "permission": "editor",
            "shared_by": uuid_a(),
        }))
        .expect("valid share request");

        assert_eq!(request.shared_with, uuid_b());
        assert_eq!(request.shared_by, uuid_a());
        assert_eq!(request.permission, SharePermission::Editor);
    }

    #[test]
    fn a_share_request_with_an_unknown_permission_is_rejected() {
        for permission in ["superuser", "owner", "admin", "read-write", ""] {
            let result = serde_json::from_value::<ShareFileRequest>(json!({
                "shared_with": uuid_b(),
                "permission": permission,
                "shared_by": uuid_a(),
            }));

            assert!(
                result.is_err(),
                "permission {permission:?} must be rejected"
            );
        }
    }

    #[test]
    fn a_share_request_missing_a_required_field_is_rejected() {
        for missing in ["shared_with", "permission", "shared_by"] {
            let mut body = json!({
                "shared_with": uuid_b(),
                "permission": "viewer",
                "shared_by": uuid_a(),
            });
            body.as_object_mut().unwrap().remove(missing);

            assert!(
                serde_json::from_value::<ShareFileRequest>(body).is_err(),
                "a request without {missing} must be rejected"
            );
        }
    }

    #[test]
    fn a_share_request_with_a_malformed_uuid_is_rejected() {
        let result = serde_json::from_value::<ShareFileRequest>(json!({
            "shared_with": "not-a-uuid",
            "permission": "viewer",
            "shared_by": uuid_a(),
        }));

        assert!(result.is_err());
    }

    /// Sharing with yourself passes the wire contract unchallenged; the
    /// storage-level counterpart of this gap is pinned in `metadata.rs`.
    #[test]
    fn a_self_share_request_parses() {
        let request: ShareFileRequest = serde_json::from_value(json!({
            "shared_with": uuid_a(),
            "permission": "viewer",
            "shared_by": uuid_a(),
        }))
        .expect("self-share is accepted by the model");

        assert_eq!(request.shared_with, request.shared_by);
    }

    #[test]
    fn unknown_fields_on_a_share_request_are_ignored() {
        let request: ShareFileRequest = serde_json::from_value(json!({
            "shared_with": uuid_b(),
            "permission": "viewer",
            "shared_by": uuid_a(),
            "expires_at": "2026-01-01T00:00:00Z",
        }))
        .expect("unknown fields are tolerated");

        // No `deny_unknown_fields`: a client that thinks it set an expiry gets
        // a permanent share instead of a 400.
        assert_eq!(request.permission, SharePermission::Viewer);
    }

    // -- Folder requests --

    #[test]
    fn a_root_folder_request_parses_without_a_parent() {
        let request: CreateFolderRequest = serde_json::from_value(json!({
            "name": "Documents",
            "owner_id": uuid_a(),
        }))
        .expect("valid create request");

        assert_eq!(request.parent_id, None);
    }

    #[test]
    fn an_explicit_null_parent_means_the_root() {
        let request: CreateFolderRequest = serde_json::from_value(json!({
            "name": "Documents",
            "parent_id": null,
            "owner_id": uuid_a(),
        }))
        .expect("valid create request");

        assert_eq!(request.parent_id, None);
    }

    #[test]
    fn a_create_folder_request_missing_a_required_field_is_rejected() {
        for missing in ["name", "owner_id"] {
            let mut body = json!({"name": "Documents", "owner_id": uuid_a()});
            body.as_object_mut().unwrap().remove(missing);

            assert!(
                serde_json::from_value::<CreateFolderRequest>(body).is_err(),
                "a request without {missing} must be rejected"
            );
        }
    }

    /// Boundary: the model imposes no length rule, so an empty name and a
    /// very long one both parse. Any limit has to come from the handler.
    #[test]
    fn folder_names_are_unvalidated_at_the_model_boundary() {
        for name in ["", " ", "../escape", &"n".repeat(4096)] {
            let request: CreateFolderRequest = serde_json::from_value(json!({
                "name": name,
                "owner_id": uuid_a(),
            }))
            .expect("names are not validated here");

            assert_eq!(request.name, name);
        }
    }

    #[test]
    fn an_empty_folder_update_parses_to_no_changes() {
        let request: UpdateFolderRequest =
            serde_json::from_value(json!({})).expect("an empty update is well-formed");

        assert!(request.name.is_none() && request.parent_id.is_none());
    }

    #[test]
    fn a_folder_update_can_carry_a_cycle_creating_parent() {
        // The model happily encodes "make this folder its own parent"; the
        // absence of a cycle check is pinned in `metadata.rs`.
        let request: UpdateFolderRequest = serde_json::from_value(json!({
            "parent_id": uuid_a(),
        }))
        .expect("valid update request");

        assert_eq!(request.parent_id, Some(uuid_a()));
    }

    #[test]
    fn a_folder_update_with_a_malformed_parent_is_rejected() {
        assert!(
            serde_json::from_value::<UpdateFolderRequest>(json!({"parent_id": "root"})).is_err()
        );
    }

    // -- Move / rename --

    /// A move to the root and a move that names no destination are the same
    /// request on the wire: both arrive as `None`.
    #[test]
    fn move_to_root_and_an_absent_destination_are_indistinguishable() {
        let absent: MoveFileRequest = serde_json::from_value(json!({})).unwrap();
        let explicit_null: MoveFileRequest =
            serde_json::from_value(json!({"folder_id": null})).unwrap();

        assert_eq!(absent.folder_id, None);
        assert_eq!(explicit_null.folder_id, None);
    }

    #[test]
    fn a_move_into_a_named_folder_parses() {
        let request: MoveFileRequest =
            serde_json::from_value(json!({"folder_id": uuid_b()})).unwrap();

        assert_eq!(request.folder_id, Some(uuid_b()));
    }

    #[test]
    fn a_rename_request_requires_a_name() {
        assert!(serde_json::from_value::<RenameFileRequest>(json!({})).is_err());
        assert!(serde_json::from_value::<RenameFileRequest>(json!({"name": 7})).is_err());
        assert_eq!(
            serde_json::from_value::<RenameFileRequest>(json!({"name": ""}))
                .unwrap()
                .name,
            "",
            "an empty rename is not rejected by the model"
        );
    }

    // -- Query models --

    #[test]
    fn list_files_query_defaults_every_field_to_none() {
        let query = actix_web::web::Query::<ListFilesQuery>::from_query("").unwrap();

        assert!(query.folder_id.is_none());
        assert!(query.owner_id.is_none());
        assert!(query.page.is_none());
        assert!(query.page_size.is_none());
        assert!(query.include_trashed.is_none());
    }

    #[test]
    fn list_files_query_parses_a_full_query_string() {
        let raw = format!(
            "folder_id={}&owner_id={}&page=2&page_size=50&include_trashed=true",
            uuid_b(),
            uuid_a()
        );
        let query = actix_web::web::Query::<ListFilesQuery>::from_query(&raw).unwrap();

        assert_eq!(query.folder_id, Some(uuid_b()));
        assert_eq!(query.owner_id, Some(uuid_a()));
        assert_eq!(query.page, Some(2));
        assert_eq!(query.page_size, Some(50));
        assert_eq!(query.include_trashed, Some(true));
    }

    /// Boundary trio for the u32 page size: max-1 and max parse, max+1 is a
    /// 400 rather than a silent clamp.
    #[test]
    fn page_size_boundary_trio() {
        for (raw, expected) in [
            ("4294967294", Some(u32::MAX - 1)),
            ("4294967295", Some(u32::MAX)),
            ("4294967296", None),
        ] {
            let parsed =
                actix_web::web::Query::<ListFilesQuery>::from_query(&format!("page_size={raw}"))
                    .ok()
                    .and_then(|q| q.page_size);

            assert_eq!(parsed, expected, "page_size={raw}");
        }
    }

    #[test]
    fn a_zero_page_size_is_accepted_by_the_query_model() {
        let query =
            actix_web::web::Query::<ListFilesQuery>::from_query("page_size=0&page=0").unwrap();

        assert_eq!(query.page_size, Some(0));
        assert_eq!(query.page, Some(0));
    }

    #[test]
    fn a_non_boolean_include_trashed_is_rejected() {
        for raw in ["yes", "1", "TRUE", ""] {
            assert!(
                actix_web::web::Query::<ListFilesQuery>::from_query(&format!(
                    "include_trashed={raw}"
                ))
                .is_err(),
                "include_trashed={raw:?} must not parse"
            );
        }
    }

    #[test]
    fn list_folders_query_parses_both_filters() {
        let raw = format!("parent_id={}&owner_id={}", uuid_b(), uuid_a());
        let query = actix_web::web::Query::<ListFoldersQuery>::from_query(&raw).unwrap();

        assert_eq!(query.parent_id, Some(uuid_b()));
        assert_eq!(query.owner_id, Some(uuid_a()));
    }

    #[test]
    fn a_malformed_uuid_in_a_query_is_rejected() {
        assert!(actix_web::web::Query::<ListFoldersQuery>::from_query("parent_id=root").is_err());
    }
}

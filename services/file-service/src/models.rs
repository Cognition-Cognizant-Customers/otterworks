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
    use serde_json::json;

    /// Every share tier the API can express.
    const ALL_TIERS: [SharePermission; 2] = [SharePermission::Viewer, SharePermission::Editor];

    /// An action a share can gate, and whether each tier is meant to be able to
    /// perform it. This is the contract the endpoints advertise (`viewer` =
    /// read-only, `editor` = read/write, owner-only for destructive ops).
    struct GatedAction {
        name: &'static str,
        viewer_allowed: bool,
        editor_allowed: bool,
    }

    const ACTION_MATRIX: [GatedAction; 8] = [
        GatedAction {
            name: "read metadata",
            viewer_allowed: true,
            editor_allowed: true,
        },
        GatedAction {
            name: "download",
            viewer_allowed: true,
            editor_allowed: true,
        },
        GatedAction {
            name: "list versions",
            viewer_allowed: true,
            editor_allowed: true,
        },
        GatedAction {
            name: "rename",
            viewer_allowed: false,
            editor_allowed: true,
        },
        GatedAction {
            name: "move",
            viewer_allowed: false,
            editor_allowed: true,
        },
        GatedAction {
            name: "trash",
            viewer_allowed: false,
            editor_allowed: true,
        },
        GatedAction {
            name: "delete permanently",
            viewer_allowed: false,
            editor_allowed: false,
        },
        GatedAction {
            name: "re-share / remove share",
            viewer_allowed: false,
            editor_allowed: false,
        },
    ];

    // -- SharePermission: parsing --

    #[test]
    fn share_permission_parses_every_canonical_tier() {
        assert_eq!(
            SharePermission::from_str_value("viewer"),
            Some(SharePermission::Viewer)
        );
        assert_eq!(
            SharePermission::from_str_value("editor"),
            Some(SharePermission::Editor)
        );
    }

    #[test]
    fn share_permission_parsing_is_case_insensitive() {
        for value in ["viewer", "Viewer", "VIEWER", "vIeWeR"] {
            assert_eq!(
                SharePermission::from_str_value(value),
                Some(SharePermission::Viewer),
                "{value:?} should parse as viewer"
            );
        }
        for value in ["editor", "Editor", "EDITOR", "eDiToR"] {
            assert_eq!(
                SharePermission::from_str_value(value),
                Some(SharePermission::Editor),
                "{value:?} should parse as editor"
            );
        }
    }

    #[test]
    fn share_permission_rejects_unknown_blank_and_padded_values() {
        for value in [
            "",
            " ",
            "viewer ",
            " viewer",
            "view",
            "viewers",
            "owner",
            "admin",
            "write",
            "read",
            "editor,viewer",
            "null",
            "VIEWER\n",
            "виewer",
        ] {
            assert_eq!(
                SharePermission::from_str_value(value),
                None,
                "{value:?} must not parse as a share tier"
            );
        }
    }

    #[test]
    fn share_permission_display_round_trips_through_from_str_value() {
        for tier in ALL_TIERS {
            let rendered = tier.to_string();
            assert_eq!(rendered, rendered.to_lowercase(), "wire form is lowercase");
            assert_eq!(
                SharePermission::from_str_value(&rendered),
                Some(tier.clone()),
                "{rendered} must round-trip"
            );
        }
        assert_eq!(SharePermission::Viewer.to_string(), "viewer");
        assert_eq!(SharePermission::Editor.to_string(), "editor");
    }

    #[test]
    fn share_permission_serde_uses_the_same_lowercase_wire_form_as_display() {
        for tier in ALL_TIERS {
            let json = serde_json::to_value(&tier).unwrap();
            assert_eq!(json, json!(tier.to_string()));
            let parsed: SharePermission = serde_json::from_value(json).unwrap();
            assert_eq!(parsed, tier);
        }
    }

    #[test]
    fn share_permission_deserialization_rejects_unknown_and_miscased_tiers() {
        // `#[serde(rename_all = "lowercase")]` is stricter than
        // `from_str_value`: over the wire, "Editor" is not accepted even though
        // the DynamoDB parser would take it.
        for value in ["Editor", "VIEWER", "owner", "", "1"] {
            assert!(
                serde_json::from_value::<SharePermission>(json!(value)).is_err(),
                "{value:?} must not deserialize"
            );
        }
        assert!(serde_json::from_value::<SharePermission>(json!(null)).is_err());
        assert!(serde_json::from_value::<SharePermission>(json!(2)).is_err());
    }

    #[test]
    fn share_permission_wire_form_and_parser_disagree_on_case() {
        // Documents a real asymmetry: a record written with "Editor" is
        // readable from DynamoDB but the same string is rejected on a request
        // body. See FINDING in
        // share_permission_should_accept_the_same_strings_everywhere.
        assert_eq!(
            SharePermission::from_str_value("Editor"),
            Some(SharePermission::Editor)
        );
        assert!(serde_json::from_value::<SharePermission>(json!("Editor")).is_err());
    }

    #[test]
    #[ignore = "FINDING: SharePermission has two different string contracts — the request body \
                (serde) accepts only lowercase while the DynamoDB parser (from_str_value) is \
                case-insensitive, so a share row written as 'Editor' by any other writer is \
                readable but not reproducible through the API"]
    fn share_permission_should_accept_the_same_strings_everywhere() {
        assert!(serde_json::from_value::<SharePermission>(json!("Editor")).is_ok());
    }

    #[test]
    fn share_permission_tiers_are_distinguishable() {
        assert_ne!(SharePermission::Viewer, SharePermission::Editor);
        assert_eq!(SharePermission::Viewer, SharePermission::Viewer.clone());
        assert!(format!("{:?}", SharePermission::Editor).contains("Editor"));
    }

    #[test]
    fn action_matrix_is_a_strict_hierarchy() {
        // Every action a viewer may take, an editor may take too; the reverse
        // does not hold, and neither tier may perform an owner-only action.
        for action in &ACTION_MATRIX {
            assert!(
                !action.viewer_allowed || action.editor_allowed,
                "{}: editor must be at least as capable as viewer",
                action.name
            );
        }
        assert!(
            ACTION_MATRIX
                .iter()
                .any(|a| a.editor_allowed && !a.viewer_allowed),
            "the matrix must contain an action that separates the tiers"
        );
        assert!(
            ACTION_MATRIX
                .iter()
                .any(|a| !a.editor_allowed && !a.viewer_allowed),
            "the matrix must contain an owner-only action"
        );
    }

    #[test]
    #[ignore = "FINDING: the share tier is stored but never enforced. No code path in \
                file-service reads FileShare::permission before mutating a file, so a `viewer` \
                share confers the same rights as `editor` (see the metadata.rs matrix tests \
                share_tier_does_not_gate_* which assert the current, permissive behavior)"]
    fn share_tiers_should_gate_the_action_matrix() {
        // A gating predicate does not exist on the type; when it lands it must
        // agree with ACTION_MATRIX for every (tier, action) pair.
        let gate = |_tier: &SharePermission, _action: &GatedAction| -> Option<bool> { None };
        for tier in ALL_TIERS {
            for action in &ACTION_MATRIX {
                let expected = match tier {
                    SharePermission::Viewer => action.viewer_allowed,
                    SharePermission::Editor => action.editor_allowed,
                };
                assert_eq!(
                    gate(&tier, action),
                    Some(expected),
                    "{tier} / {} is not gated by the share tier",
                    action.name
                );
            }
        }
    }

    // -- Serialized shapes --

    fn sample_file(id: Uuid, owner: Uuid) -> FileMetadata {
        FileMetadata {
            id,
            name: "quarterly.pdf".into(),
            mime_type: "application/pdf".into(),
            size_bytes: 2048,
            s3_key: format!("files/{owner}/{id}"),
            folder_id: None,
            owner_id: owner,
            version: 1,
            is_trashed: false,
            created_at: DateTime::from_timestamp(1_700_000_000, 0).unwrap(),
            updated_at: DateTime::from_timestamp(1_700_000_060, 0).unwrap(),
        }
    }

    #[test]
    fn file_metadata_serializes_every_field_and_round_trips() {
        let id = Uuid::nil();
        let owner = Uuid::from_u128(7);
        let json = serde_json::to_value(sample_file(id, owner)).unwrap();

        assert_eq!(json["id"], id.to_string());
        assert_eq!(json["name"], "quarterly.pdf");
        assert_eq!(json["mime_type"], "application/pdf");
        assert_eq!(json["size_bytes"], 2048);
        assert_eq!(json["owner_id"], owner.to_string());
        assert_eq!(json["version"], 1);
        assert_eq!(json["is_trashed"], false);
        assert_eq!(json["folder_id"], serde_json::Value::Null);
        assert_eq!(json.as_object().unwrap().len(), 11);

        let parsed: FileMetadata = serde_json::from_value(json).unwrap();
        assert_eq!(parsed.id, id);
        assert_eq!(parsed.size_bytes, 2048);
    }

    #[test]
    fn file_metadata_carries_boundary_numeric_values_without_loss() {
        let mut file = sample_file(Uuid::from_u128(1), Uuid::from_u128(2));
        for size in [0u64, 1, u64::MAX - 1, u64::MAX] {
            file.size_bytes = size;
            let round_tripped: FileMetadata =
                serde_json::from_str(&serde_json::to_string(&file).unwrap()).unwrap();
            assert_eq!(round_tripped.size_bytes, size);
        }
        for version in [0u32, 1, u32::MAX] {
            file.version = version;
            let round_tripped: FileMetadata =
                serde_json::from_str(&serde_json::to_string(&file).unwrap()).unwrap();
            assert_eq!(round_tripped.version, version);
        }
    }

    #[test]
    fn file_metadata_rejects_a_negative_size() {
        let mut json =
            serde_json::to_value(sample_file(Uuid::from_u128(1), Uuid::from_u128(2))).unwrap();
        json["size_bytes"] = json!(-1);
        assert!(serde_json::from_value::<FileMetadata>(json).is_err());
    }

    #[test]
    fn file_detail_response_flattens_the_file_alongside_its_shares() {
        let file_id = Uuid::from_u128(11);
        let owner = Uuid::from_u128(12);
        let share = FileShare {
            id: Uuid::from_u128(13),
            file_id,
            shared_with: Uuid::from_u128(14),
            permission: SharePermission::Viewer,
            shared_by: owner,
            created_at: DateTime::from_timestamp(1_700_000_000, 0).unwrap(),
        };
        let json = serde_json::to_value(FileDetailResponse {
            file: sample_file(file_id, owner),
            shared_with: vec![share],
        })
        .unwrap();

        // Flattened: the file's fields sit at the top level, not under "file".
        assert!(json.get("file").is_none());
        assert_eq!(json["id"], file_id.to_string());
        assert_eq!(json["shared_with"][0]["permission"], "viewer");
        assert_eq!(json["shared_with"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn file_detail_response_with_no_shares_emits_an_empty_array() {
        let json = serde_json::to_value(FileDetailResponse {
            file: sample_file(Uuid::from_u128(1), Uuid::from_u128(2)),
            shared_with: vec![],
        })
        .unwrap();
        assert_eq!(json["shared_with"], json!([]));
    }

    #[test]
    fn folder_and_version_shapes_are_stable() {
        let folder = Folder {
            id: Uuid::from_u128(21),
            name: "Documents".into(),
            parent_id: Some(Uuid::from_u128(22)),
            owner_id: Uuid::from_u128(23),
            created_at: DateTime::from_timestamp(1_700_000_000, 0).unwrap(),
            updated_at: DateTime::from_timestamp(1_700_000_000, 0).unwrap(),
        };
        let json = serde_json::to_value(&folder).unwrap();
        assert_eq!(json["parent_id"], Uuid::from_u128(22).to_string());
        let parsed: Folder = serde_json::from_value(json).unwrap();
        assert_eq!(parsed.parent_id, folder.parent_id);

        let version = FileVersion {
            file_id: Uuid::from_u128(24),
            version: 3,
            s3_key: "files/x/y".into(),
            size_bytes: 10,
            created_by: Uuid::from_u128(25),
            created_at: DateTime::from_timestamp(1_700_000_000, 0).unwrap(),
        };
        let json = serde_json::to_value(&version).unwrap();
        assert_eq!(json["version"], 3);
        assert_eq!(json.as_object().unwrap().len(), 6);
    }

    // -- Request deserialization: positive, negative, boundary --

    #[test]
    fn share_file_request_requires_all_three_fields() {
        let shared_with = Uuid::from_u128(31);
        let shared_by = Uuid::from_u128(32);
        let body = json!({
            "shared_with": shared_with,
            "permission": "editor",
            "shared_by": shared_by,
        });
        let parsed: ShareFileRequest = serde_json::from_value(body).unwrap();
        assert_eq!(parsed.shared_with, shared_with);
        assert_eq!(parsed.permission, SharePermission::Editor);
        assert_eq!(parsed.shared_by, shared_by);

        for missing in ["shared_with", "permission", "shared_by"] {
            let mut body = json!({
                "shared_with": shared_with,
                "permission": "editor",
                "shared_by": shared_by,
            });
            body.as_object_mut().unwrap().remove(missing);
            assert!(
                serde_json::from_value::<ShareFileRequest>(body).is_err(),
                "missing {missing} must be rejected"
            );
        }
    }

    #[test]
    fn share_file_request_rejects_an_unknown_permission_or_malformed_uuid() {
        let body = json!({
            "shared_with": Uuid::from_u128(31),
            "permission": "owner",
            "shared_by": Uuid::from_u128(32),
        });
        assert!(serde_json::from_value::<ShareFileRequest>(body).is_err());

        let body = json!({
            "shared_with": "not-a-uuid",
            "permission": "viewer",
            "shared_by": Uuid::from_u128(32),
        });
        assert!(serde_json::from_value::<ShareFileRequest>(body).is_err());
    }

    #[test]
    fn share_file_request_allows_sharing_with_yourself() {
        // Documents current behavior: nothing in the model prevents
        // shared_with == shared_by. See FINDING in
        // share_with_self_should_be_rejected.
        let me = Uuid::from_u128(33);
        let parsed: ShareFileRequest = serde_json::from_value(json!({
            "shared_with": me,
            "permission": "viewer",
            "shared_by": me,
        }))
        .unwrap();
        assert_eq!(parsed.shared_with, parsed.shared_by);
    }

    #[test]
    #[ignore = "FINDING: share-with-self is accepted end to end (model, handler and metadata all \
                allow shared_with == shared_by == owner), producing a self-share row that shows \
                the owner's own file in their 'Shared with me' list"]
    fn share_with_self_should_be_rejected() {
        let me = Uuid::from_u128(33);
        assert!(serde_json::from_value::<ShareFileRequest>(json!({
            "shared_with": me,
            "permission": "viewer",
            "shared_by": me,
        }))
        .is_err());
    }

    #[test]
    fn list_files_query_fields_are_all_optional() {
        let empty: ListFilesQuery = serde_json::from_value(json!({})).unwrap();
        assert!(empty.folder_id.is_none());
        assert!(empty.owner_id.is_none());
        assert!(empty.page.is_none());
        assert!(empty.page_size.is_none());
        assert!(empty.include_trashed.is_none());

        let populated: ListFilesQuery = serde_json::from_value(json!({
            "folder_id": Uuid::from_u128(41),
            "owner_id": Uuid::from_u128(42),
            "page": 2,
            "page_size": 100,
            "include_trashed": true,
        }))
        .unwrap();
        assert_eq!(populated.page, Some(2));
        assert_eq!(populated.page_size, Some(100));
        assert_eq!(populated.include_trashed, Some(true));
    }

    #[test]
    fn list_files_query_page_size_boundaries() {
        // The handler clamps with `.min(100)` and `.max(1)`; the model itself
        // must carry 0, the clamp boundary, and one past it unchanged.
        for (page, page_size) in [
            (0u32, 0u32),
            (1, 99),
            (1, 100),
            (1, 101),
            (u32::MAX, u32::MAX),
        ] {
            let query: ListFilesQuery =
                serde_json::from_value(json!({"page": page, "page_size": page_size})).unwrap();
            assert_eq!(query.page, Some(page));
            assert_eq!(query.page_size, Some(page_size));
        }
    }

    #[test]
    fn list_files_query_rejects_negative_or_non_numeric_paging() {
        for body in [
            json!({"page": -1}),
            json!({"page_size": -1}),
            json!({"page": "2"}),
            json!({"page_size": 1.5}),
            json!({"include_trashed": "yes"}),
            json!({"owner_id": "not-a-uuid"}),
        ] {
            assert!(
                serde_json::from_value::<ListFilesQuery>(body.clone()).is_err(),
                "{body} must be rejected"
            );
        }
    }

    #[test]
    fn create_folder_request_requires_name_and_owner_but_not_parent() {
        let owner = Uuid::from_u128(51);
        let root: CreateFolderRequest =
            serde_json::from_value(json!({"name": "Root", "owner_id": owner})).unwrap();
        assert!(root.parent_id.is_none());

        let nested: CreateFolderRequest = serde_json::from_value(
            json!({"name": "Child", "parent_id": Uuid::from_u128(52), "owner_id": owner}),
        )
        .unwrap();
        assert_eq!(nested.parent_id, Some(Uuid::from_u128(52)));

        assert!(serde_json::from_value::<CreateFolderRequest>(json!({"name": "x"})).is_err());
        assert!(serde_json::from_value::<CreateFolderRequest>(json!({"owner_id": owner})).is_err());
    }

    #[test]
    fn create_folder_request_accepts_a_blank_name() {
        // Documents current behavior: unlike rename, folder creation has no
        // name validation at any layer. See FINDING in
        // create_folder_should_reject_a_blank_name.
        let parsed: CreateFolderRequest =
            serde_json::from_value(json!({"name": "   ", "owner_id": Uuid::from_u128(53)}))
                .unwrap();
        assert_eq!(parsed.name, "   ");
    }

    #[test]
    #[ignore = "FINDING: create_folder accepts an empty or whitespace-only name (rename_file \
                rejects one), so the tree can contain unnameable folders"]
    fn create_folder_should_reject_a_blank_name() {
        assert!(serde_json::from_value::<CreateFolderRequest>(
            json!({"name": "", "owner_id": Uuid::from_u128(53)})
        )
        .is_err());
    }

    #[test]
    fn update_folder_request_distinguishes_absent_from_null() {
        let empty: UpdateFolderRequest = serde_json::from_value(json!({})).unwrap();
        assert!(empty.name.is_none() && empty.parent_id.is_none());

        let renamed: UpdateFolderRequest = serde_json::from_value(json!({"name": "New"})).unwrap();
        assert_eq!(renamed.name.as_deref(), Some("New"));

        let nulled: UpdateFolderRequest =
            serde_json::from_value(json!({"parent_id": null})).unwrap();
        assert!(
            nulled.parent_id.is_none(),
            "an explicit null is indistinguishable from an absent field, so a folder cannot be \
             moved back to the root through this endpoint"
        );
    }

    #[test]
    fn move_file_request_treats_null_as_move_to_root() {
        let to_root: MoveFileRequest = serde_json::from_value(json!({"folder_id": null})).unwrap();
        assert!(to_root.folder_id.is_none());

        let to_folder: MoveFileRequest =
            serde_json::from_value(json!({"folder_id": Uuid::from_u128(61)})).unwrap();
        assert_eq!(to_folder.folder_id, Some(Uuid::from_u128(61)));

        assert!(serde_json::from_value::<MoveFileRequest>(json!({"folder_id": ""})).is_err());
    }

    #[test]
    fn rename_file_request_requires_a_string_name() {
        let parsed: RenameFileRequest = serde_json::from_value(json!({"name": "a.txt"})).unwrap();
        assert_eq!(parsed.name, "a.txt");
        assert!(serde_json::from_value::<RenameFileRequest>(json!({})).is_err());
        assert!(serde_json::from_value::<RenameFileRequest>(json!({"name": 1})).is_err());

        let blank: RenameFileRequest = serde_json::from_value(json!({"name": "   "})).unwrap();
        assert_eq!(
            blank.name.trim(),
            "",
            "the model accepts blank names; the handler is what rejects them"
        );
    }

    #[test]
    fn activity_query_limit_boundaries() {
        let default: ActivityQuery = serde_json::from_value(json!({})).unwrap();
        assert!(default.limit.is_none(), "handler default is 20");
        // Handler clamps with `.min(50)`.
        for limit in [0u32, 1, 49, 50, 51, u32::MAX] {
            let query: ActivityQuery = serde_json::from_value(json!({"limit": limit})).unwrap();
            assert_eq!(query.limit, Some(limit));
        }
        assert!(serde_json::from_value::<ActivityQuery>(json!({"limit": -1})).is_err());
    }

    // -- Response shapes --

    #[test]
    fn list_responses_expose_their_paging_envelope() {
        let json = serde_json::to_value(ListFilesResponse {
            files: vec![],
            total: 0,
            page: 1,
            page_size: 50,
        })
        .unwrap();
        assert_eq!(json["files"], json!([]));
        assert_eq!(json["total"], 0);
        assert_eq!(json["page"], 1);
        assert_eq!(json["page_size"], 50);

        let json = serde_json::to_value(ListFoldersResponse { folders: vec![] }).unwrap();
        assert_eq!(json["folders"], json!([]));

        let json = serde_json::to_value(ListVersionsResponse { versions: vec![] }).unwrap();
        assert_eq!(json["versions"], json!([]));
    }

    #[test]
    fn health_upload_download_and_share_responses_have_stable_keys() {
        let json = serde_json::to_value(HealthResponse {
            status: "healthy".into(),
            service: "file-service".into(),
            version: "0.1.0".into(),
        })
        .unwrap();
        assert_eq!(json["status"], "healthy");
        assert_eq!(json["service"], "file-service");

        let json = serde_json::to_value(UploadResponse {
            file: sample_file(Uuid::from_u128(1), Uuid::from_u128(2)),
        })
        .unwrap();
        assert!(json["file"]["s3_key"].is_string());

        let json = serde_json::to_value(DownloadResponse {
            url: "https://example.test/x".into(),
            expires_in_secs: 3600,
        })
        .unwrap();
        assert_eq!(json["expires_in_secs"], 3600);

        let json = serde_json::to_value(ShareFileResponse {
            share: FileShare {
                id: Uuid::from_u128(71),
                file_id: Uuid::from_u128(72),
                shared_with: Uuid::from_u128(73),
                permission: SharePermission::Editor,
                shared_by: Uuid::from_u128(74),
                created_at: DateTime::from_timestamp(1_700_000_000, 0).unwrap(),
            },
        })
        .unwrap();
        assert_eq!(json["share"]["permission"], "editor");
    }

    #[test]
    fn activity_item_renames_its_type_field_for_the_wire() {
        let json = serde_json::to_value(ActivityResponse {
            items: vec![ActivityItem {
                id: "upload-1".into(),
                activity_type: "upload".into(),
                description: "Uploaded a.txt".into(),
                actor_name: "You".into(),
                resource_name: "a.txt".into(),
                resource_type: "file".into(),
                resource_id: Uuid::from_u128(81).to_string(),
                created_at: "2026-01-01T00:00:00+00:00".into(),
            }],
        })
        .unwrap();
        let item = &json["items"][0];
        assert_eq!(item["type"], "upload");
        assert!(
            item.get("activity_type").is_none(),
            "the Rust field name must not leak onto the wire"
        );
    }
}

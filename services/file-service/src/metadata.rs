use aws_sdk_dynamodb::types::AttributeValue;
use chrono::Utc;
use uuid::Uuid;

use crate::config::AwsConfig;
use crate::errors::ServiceError;
use crate::models::{FileMetadata, FileShare, FileVersion, Folder, SharePermission};

/// Check if an AWS SDK error is a ConditionalCheckFailedException.
fn is_conditional_check_failed<E: std::fmt::Debug>(
    err: &aws_sdk_dynamodb::error::SdkError<E>,
) -> bool {
    matches!(err, aws_sdk_dynamodb::error::SdkError::ServiceError(se)
        if format!("{:?}", se.err()).contains("ConditionalCheckFailed"))
}

/// Client for DynamoDB metadata operations.
#[derive(Clone)]
pub struct MetadataClient {
    pub client: aws_sdk_dynamodb::Client,
    pub files_table: String,
    pub folders_table: String,
    pub versions_table: String,
    pub shares_table: String,
}

impl MetadataClient {
    pub async fn new(config: &AwsConfig) -> Self {
        let mut aws_config_builder = aws_config::defaults(aws_config::BehaviorVersion::latest())
            .region(aws_config::Region::new(config.region.clone()));

        if let Some(endpoint) = &config.endpoint_url {
            aws_config_builder = aws_config_builder.endpoint_url(endpoint);
        }

        let aws_config = aws_config_builder.load().await;
        let client = aws_sdk_dynamodb::Client::new(&aws_config);

        Self {
            client,
            files_table: config.dynamodb_table.clone(),
            folders_table: config.dynamodb_folders_table.clone(),
            versions_table: config.dynamodb_versions_table.clone(),
            shares_table: config.dynamodb_shares_table.clone(),
        }
    }

    // -- File Metadata --

    pub async fn put_file(&self, file: &FileMetadata) -> Result<(), ServiceError> {
        let mut item = std::collections::HashMap::new();
        item.insert("id".into(), AttributeValue::S(file.id.to_string()));
        item.insert("name".into(), AttributeValue::S(file.name.clone()));
        item.insert(
            "mime_type".into(),
            AttributeValue::S(file.mime_type.clone()),
        );
        item.insert(
            "size_bytes".into(),
            AttributeValue::N(file.size_bytes.to_string()),
        );
        item.insert("s3_key".into(), AttributeValue::S(file.s3_key.clone()));
        item.insert(
            "owner_id".into(),
            AttributeValue::S(file.owner_id.to_string()),
        );
        item.insert(
            "version".into(),
            AttributeValue::N(file.version.to_string()),
        );
        item.insert("is_trashed".into(), AttributeValue::Bool(file.is_trashed));
        item.insert(
            "created_at".into(),
            AttributeValue::S(file.created_at.to_rfc3339()),
        );
        item.insert(
            "updated_at".into(),
            AttributeValue::S(file.updated_at.to_rfc3339()),
        );

        if let Some(folder_id) = &file.folder_id {
            item.insert("folder_id".into(), AttributeValue::S(folder_id.to_string()));
        }

        self.client
            .put_item()
            .table_name(&self.files_table)
            .set_item(Some(item))
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;

        Ok(())
    }

    pub async fn get_file(&self, file_id: &Uuid) -> Result<FileMetadata, ServiceError> {
        let result = self
            .client
            .get_item()
            .table_name(&self.files_table)
            .key("id", AttributeValue::S(file_id.to_string()))
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;

        let item = result
            .item()
            .ok_or_else(|| ServiceError::FileNotFound(file_id.to_string()))?;

        parse_file_metadata(item)
    }

    pub async fn delete_file(&self, file_id: &Uuid) -> Result<(), ServiceError> {
        self.client
            .delete_item()
            .table_name(&self.files_table)
            .key("id", AttributeValue::S(file_id.to_string()))
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;
        Ok(())
    }

    pub async fn trash_file(&self, file_id: &Uuid) -> Result<FileMetadata, ServiceError> {
        let now = Utc::now();
        self.client
            .update_item()
            .table_name(&self.files_table)
            .key("id", AttributeValue::S(file_id.to_string()))
            .update_expression("SET is_trashed = :t, updated_at = :u")
            .condition_expression("attribute_exists(id)")
            .expression_attribute_values(":t", AttributeValue::Bool(true))
            .expression_attribute_values(":u", AttributeValue::S(now.to_rfc3339()))
            .send()
            .await
            .map_err(|e| {
                if is_conditional_check_failed(&e) {
                    return ServiceError::FileNotFound(file_id.to_string());
                }
                ServiceError::DynamoError(e.to_string())
            })?;

        self.get_file(file_id).await
    }

    pub async fn restore_file(&self, file_id: &Uuid) -> Result<FileMetadata, ServiceError> {
        let now = Utc::now();
        self.client
            .update_item()
            .table_name(&self.files_table)
            .key("id", AttributeValue::S(file_id.to_string()))
            .update_expression("SET is_trashed = :t, updated_at = :u")
            .condition_expression("attribute_exists(id)")
            .expression_attribute_values(":t", AttributeValue::Bool(false))
            .expression_attribute_values(":u", AttributeValue::S(now.to_rfc3339()))
            .send()
            .await
            .map_err(|e| {
                if is_conditional_check_failed(&e) {
                    return ServiceError::FileNotFound(file_id.to_string());
                }
                ServiceError::DynamoError(e.to_string())
            })?;

        self.get_file(file_id).await
    }

    pub async fn rename_file(
        &self,
        file_id: &Uuid,
        name: &str,
    ) -> Result<FileMetadata, ServiceError> {
        let now = Utc::now();
        self.client
            .update_item()
            .table_name(&self.files_table)
            .key("id", AttributeValue::S(file_id.to_string()))
            .update_expression("SET #n = :n, updated_at = :u")
            .condition_expression("attribute_exists(id)")
            .expression_attribute_names("#n", "name")
            .expression_attribute_values(":n", AttributeValue::S(name.to_string()))
            .expression_attribute_values(":u", AttributeValue::S(now.to_rfc3339()))
            .send()
            .await
            .map_err(|e| {
                if is_conditional_check_failed(&e) {
                    return ServiceError::FileNotFound(file_id.to_string());
                }
                ServiceError::DynamoError(e.to_string())
            })?;

        self.get_file(file_id).await
    }

    pub async fn move_file(
        &self,
        file_id: &Uuid,
        folder_id: Option<Uuid>,
    ) -> Result<FileMetadata, ServiceError> {
        let now = Utc::now();
        let mut update_builder = self
            .client
            .update_item()
            .table_name(&self.files_table)
            .key("id", AttributeValue::S(file_id.to_string()))
            .condition_expression("attribute_exists(id)")
            .expression_attribute_values(":u", AttributeValue::S(now.to_rfc3339()));

        if let Some(fid) = &folder_id {
            update_builder = update_builder
                .update_expression("SET folder_id = :f, updated_at = :u")
                .expression_attribute_values(":f", AttributeValue::S(fid.to_string()));
        } else {
            update_builder =
                update_builder.update_expression("SET updated_at = :u REMOVE folder_id");
        }

        update_builder.send().await.map_err(|e| {
            if is_conditional_check_failed(&e) {
                return ServiceError::FileNotFound(file_id.to_string());
            }
            ServiceError::DynamoError(e.to_string())
        })?;

        self.get_file(file_id).await
    }

    pub async fn list_trashed(
        &self,
        owner_id: Option<Uuid>,
    ) -> Result<Vec<FileMetadata>, ServiceError> {
        let mut filter_parts = vec!["is_trashed = :trashed".to_string()];
        let mut scan_builder = self
            .client
            .scan()
            .table_name(&self.files_table)
            .expression_attribute_values(":trashed", AttributeValue::Bool(true));

        if let Some(oid) = &owner_id {
            filter_parts.push("owner_id = :owner_id".to_string());
            scan_builder = scan_builder
                .expression_attribute_values(":owner_id", AttributeValue::S(oid.to_string()));
        }

        scan_builder = scan_builder.filter_expression(filter_parts.join(" AND "));

        let mut paginator = scan_builder.into_paginator().send();
        let mut files = Vec::new();
        while let Some(page) = paginator.next().await {
            let page = page.map_err(|e| ServiceError::DynamoError(e.to_string()))?;
            if let Some(items) = page.items {
                for item in &items {
                    files.push(parse_file_metadata(item)?);
                }
            }
        }

        files.sort_by_key(|f| std::cmp::Reverse(f.updated_at));
        Ok(files)
    }

    pub async fn list_files(
        &self,
        folder_id: Option<Uuid>,
        owner_id: Option<Uuid>,
        include_trashed: bool,
    ) -> Result<Vec<FileMetadata>, ServiceError> {
        let mut scan_builder = self.client.scan().table_name(&self.files_table);

        let mut filter_parts: Vec<String> = Vec::new();

        if let Some(fid) = &folder_id {
            filter_parts.push("folder_id = :folder_id".to_string());
            scan_builder = scan_builder
                .expression_attribute_values(":folder_id", AttributeValue::S(fid.to_string()));
        }
        if let Some(oid) = &owner_id {
            filter_parts.push("owner_id = :owner_id".to_string());
            scan_builder = scan_builder
                .expression_attribute_values(":owner_id", AttributeValue::S(oid.to_string()));
        }
        if !include_trashed {
            filter_parts.push("is_trashed = :trashed".to_string());
            scan_builder =
                scan_builder.expression_attribute_values(":trashed", AttributeValue::Bool(false));
        }

        if !filter_parts.is_empty() {
            scan_builder = scan_builder.filter_expression(filter_parts.join(" AND "));
        }

        // Use the SDK paginator to handle DynamoDB's 1MB-per-Scan limit automatically
        let mut paginator = scan_builder.into_paginator().send();
        let mut files = Vec::new();
        while let Some(page) = paginator.next().await {
            let page = page.map_err(|e| ServiceError::DynamoError(e.to_string()))?;
            for item in page.items() {
                files.push(parse_file_metadata(item)?);
            }
        }
        Ok(files)
    }

    // -- Folder --

    pub async fn put_folder(&self, folder: &Folder) -> Result<(), ServiceError> {
        let mut item = std::collections::HashMap::new();
        item.insert("id".into(), AttributeValue::S(folder.id.to_string()));
        item.insert("name".into(), AttributeValue::S(folder.name.clone()));
        item.insert(
            "owner_id".into(),
            AttributeValue::S(folder.owner_id.to_string()),
        );
        item.insert(
            "created_at".into(),
            AttributeValue::S(folder.created_at.to_rfc3339()),
        );
        item.insert(
            "updated_at".into(),
            AttributeValue::S(folder.updated_at.to_rfc3339()),
        );

        if let Some(pid) = &folder.parent_id {
            item.insert("parent_id".into(), AttributeValue::S(pid.to_string()));
        }

        self.client
            .put_item()
            .table_name(&self.folders_table)
            .set_item(Some(item))
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;

        Ok(())
    }

    pub async fn get_folder(&self, folder_id: &Uuid) -> Result<Folder, ServiceError> {
        let result = self
            .client
            .get_item()
            .table_name(&self.folders_table)
            .key("id", AttributeValue::S(folder_id.to_string()))
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;

        let item = result
            .item()
            .ok_or_else(|| ServiceError::FolderNotFound(folder_id.to_string()))?;

        parse_folder(item)
    }

    pub async fn update_folder(
        &self,
        folder_id: &Uuid,
        name: Option<String>,
        parent_id: Option<Uuid>,
    ) -> Result<Folder, ServiceError> {
        let now = Utc::now();
        let mut update_parts = vec!["updated_at = :u".to_string()];
        let mut builder = self
            .client
            .update_item()
            .table_name(&self.folders_table)
            .key("id", AttributeValue::S(folder_id.to_string()))
            .condition_expression("attribute_exists(id)")
            .expression_attribute_values(":u", AttributeValue::S(now.to_rfc3339()));

        if let Some(n) = &name {
            update_parts.push("#n = :n".to_string());
            builder = builder
                .expression_attribute_names("#n", "name")
                .expression_attribute_values(":n", AttributeValue::S(n.clone()));
        }
        if let Some(pid) = &parent_id {
            update_parts.push("parent_id = :p".to_string());
            builder = builder.expression_attribute_values(":p", AttributeValue::S(pid.to_string()));
        }

        builder = builder.update_expression(format!("SET {}", update_parts.join(", ")));

        builder.send().await.map_err(|e| {
            if is_conditional_check_failed(&e) {
                return ServiceError::FolderNotFound(folder_id.to_string());
            }
            ServiceError::DynamoError(e.to_string())
        })?;

        self.get_folder(folder_id).await
    }

    pub async fn delete_folder(&self, folder_id: &Uuid) -> Result<(), ServiceError> {
        self.client
            .delete_item()
            .table_name(&self.folders_table)
            .key("id", AttributeValue::S(folder_id.to_string()))
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;
        Ok(())
    }

    pub async fn list_folders(
        &self,
        parent_id: Option<Uuid>,
        owner_id: Option<Uuid>,
    ) -> Result<Vec<Folder>, ServiceError> {
        let mut scan_builder = self.client.scan().table_name(&self.folders_table);

        let mut filter_parts: Vec<String> = Vec::new();

        match &parent_id {
            Some(pid) => {
                filter_parts.push("parent_id = :parent_id".to_string());
                scan_builder = scan_builder
                    .expression_attribute_values(":parent_id", AttributeValue::S(pid.to_string()));
            }
            None => {
                filter_parts.push("attribute_not_exists(parent_id)".to_string());
            }
        }
        if let Some(oid) = &owner_id {
            filter_parts.push("owner_id = :owner_id".to_string());
            scan_builder = scan_builder
                .expression_attribute_values(":owner_id", AttributeValue::S(oid.to_string()));
        }

        if !filter_parts.is_empty() {
            scan_builder = scan_builder.filter_expression(filter_parts.join(" AND "));
        }

        let mut paginator = scan_builder.into_paginator().send();
        let mut folders = Vec::new();
        while let Some(page) = paginator.next().await {
            let page = page.map_err(|e| ServiceError::DynamoError(e.to_string()))?;
            for item in page.items() {
                folders.push(parse_folder(item)?);
            }
        }
        Ok(folders)
    }

    // -- File Versions --

    pub async fn put_version(&self, version: &FileVersion) -> Result<(), ServiceError> {
        let mut item = std::collections::HashMap::new();
        item.insert(
            "file_id".into(),
            AttributeValue::S(version.file_id.to_string()),
        );
        item.insert(
            "version".into(),
            AttributeValue::N(version.version.to_string()),
        );
        item.insert("s3_key".into(), AttributeValue::S(version.s3_key.clone()));
        item.insert(
            "size_bytes".into(),
            AttributeValue::N(version.size_bytes.to_string()),
        );
        item.insert(
            "created_by".into(),
            AttributeValue::S(version.created_by.to_string()),
        );
        item.insert(
            "created_at".into(),
            AttributeValue::S(version.created_at.to_rfc3339()),
        );

        self.client
            .put_item()
            .table_name(&self.versions_table)
            .set_item(Some(item))
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;

        Ok(())
    }

    pub async fn list_versions(&self, file_id: &Uuid) -> Result<Vec<FileVersion>, ServiceError> {
        let result = self
            .client
            .query()
            .table_name(&self.versions_table)
            .key_condition_expression("file_id = :fid")
            .expression_attribute_values(":fid", AttributeValue::S(file_id.to_string()))
            .scan_index_forward(false)
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;

        let items = result.items();
        let mut versions = Vec::with_capacity(items.len());
        for item in items {
            versions.push(parse_file_version(item)?);
        }
        Ok(versions)
    }

    // -- File Shares --

    pub async fn put_share(&self, share: &FileShare) -> Result<(), ServiceError> {
        let mut item = std::collections::HashMap::new();
        item.insert("id".into(), AttributeValue::S(share.id.to_string()));
        item.insert(
            "file_id".into(),
            AttributeValue::S(share.file_id.to_string()),
        );
        item.insert(
            "shared_with".into(),
            AttributeValue::S(share.shared_with.to_string()),
        );
        item.insert(
            "permission".into(),
            AttributeValue::S(share.permission.to_string()),
        );
        item.insert(
            "shared_by".into(),
            AttributeValue::S(share.shared_by.to_string()),
        );
        item.insert(
            "created_at".into(),
            AttributeValue::S(share.created_at.to_rfc3339()),
        );

        self.client
            .put_item()
            .table_name(&self.shares_table)
            .set_item(Some(item))
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;

        Ok(())
    }

    pub async fn find_existing_share(
        &self,
        file_id: &Uuid,
        shared_with: &Uuid,
    ) -> Result<Option<FileShare>, ServiceError> {
        let mut paginator = self
            .client
            .scan()
            .table_name(&self.shares_table)
            .filter_expression("file_id = :fid AND shared_with = :uid")
            .expression_attribute_values(":fid", AttributeValue::S(file_id.to_string()))
            .expression_attribute_values(":uid", AttributeValue::S(shared_with.to_string()))
            .into_paginator()
            .items()
            .send();

        if let Some(item) = paginator.next().await {
            let item = item.map_err(|e| ServiceError::DynamoError(e.to_string()))?;
            return Ok(Some(parse_file_share(&item)?));
        }
        Ok(None)
    }

    pub async fn list_shares_for_user(
        &self,
        user_id: &Uuid,
    ) -> Result<Vec<FileShare>, ServiceError> {
        let mut paginator = self
            .client
            .scan()
            .table_name(&self.shares_table)
            .filter_expression("shared_with = :uid")
            .expression_attribute_values(":uid", AttributeValue::S(user_id.to_string()))
            .into_paginator()
            .send();

        let mut shares = Vec::new();
        while let Some(page) = paginator.next().await {
            let page = page.map_err(|e| ServiceError::DynamoError(e.to_string()))?;
            if let Some(items) = page.items {
                for item in &items {
                    shares.push(parse_file_share(item)?);
                }
            }
        }
        Ok(shares)
    }

    pub async fn list_shares_by_owner(
        &self,
        owner_id: &Uuid,
    ) -> Result<Vec<FileShare>, ServiceError> {
        let mut paginator = self
            .client
            .scan()
            .table_name(&self.shares_table)
            .filter_expression("shared_by = :uid")
            .expression_attribute_values(":uid", AttributeValue::S(owner_id.to_string()))
            .into_paginator()
            .items()
            .send();

        let mut shares = Vec::new();
        while let Some(item) = paginator.next().await {
            let item = item.map_err(|e| ServiceError::DynamoError(e.to_string()))?;
            shares.push(parse_file_share(&item)?);
        }
        Ok(shares)
    }

    pub async fn delete_share(&self, share_id: &Uuid) -> Result<(), ServiceError> {
        self.client
            .delete_item()
            .table_name(&self.shares_table)
            .key("id", AttributeValue::S(share_id.to_string()))
            .send()
            .await
            .map_err(|e| ServiceError::DynamoError(e.to_string()))?;
        Ok(())
    }

    pub async fn list_shares(&self, file_id: &Uuid) -> Result<Vec<FileShare>, ServiceError> {
        let mut shares = Vec::new();
        let mut paginator = self
            .client
            .scan()
            .table_name(&self.shares_table)
            .filter_expression("file_id = :fid")
            .expression_attribute_values(":fid", AttributeValue::S(file_id.to_string()))
            .into_paginator()
            .items()
            .send();

        while let Some(item) = paginator.next().await {
            let item = item.map_err(|e| ServiceError::DynamoError(e.to_string()))?;
            shares.push(parse_file_share(&item)?);
        }
        Ok(shares)
    }
}

// -- Parsing helpers --

fn get_s(
    item: &std::collections::HashMap<String, AttributeValue>,
    key: &str,
) -> Result<String, ServiceError> {
    item.get(key)
        .and_then(|v| v.as_s().ok())
        .map(|s| s.to_string())
        .ok_or_else(|| ServiceError::DynamoError(format!("missing field: {key}")))
}

fn get_n_u64(
    item: &std::collections::HashMap<String, AttributeValue>,
    key: &str,
) -> Result<u64, ServiceError> {
    item.get(key)
        .and_then(|v| v.as_n().ok())
        .and_then(|n| n.parse::<u64>().ok())
        .ok_or_else(|| ServiceError::DynamoError(format!("missing numeric field: {key}")))
}

fn get_n_u32(
    item: &std::collections::HashMap<String, AttributeValue>,
    key: &str,
) -> Result<u32, ServiceError> {
    item.get(key)
        .and_then(|v| v.as_n().ok())
        .and_then(|n| n.parse::<u32>().ok())
        .ok_or_else(|| ServiceError::DynamoError(format!("missing numeric field: {key}")))
}

fn get_bool(
    item: &std::collections::HashMap<String, AttributeValue>,
    key: &str,
) -> Result<bool, ServiceError> {
    item.get(key)
        .and_then(|v| v.as_bool().ok())
        .copied()
        .ok_or_else(|| ServiceError::DynamoError(format!("missing bool field: {key}")))
}

fn get_optional_s(
    item: &std::collections::HashMap<String, AttributeValue>,
    key: &str,
) -> Option<String> {
    item.get(key)
        .and_then(|v| v.as_s().ok())
        .map(|s| s.to_string())
}

fn parse_uuid(s: &str) -> Result<uuid::Uuid, ServiceError> {
    s.parse::<uuid::Uuid>()
        .map_err(|e| ServiceError::DynamoError(format!("invalid UUID: {e}")))
}

fn parse_datetime(s: &str) -> Result<chrono::DateTime<chrono::Utc>, ServiceError> {
    chrono::DateTime::parse_from_rfc3339(s)
        .map(|dt| dt.with_timezone(&chrono::Utc))
        .map_err(|e| ServiceError::DynamoError(format!("invalid datetime: {e}")))
}

fn parse_file_metadata(
    item: &std::collections::HashMap<String, AttributeValue>,
) -> Result<FileMetadata, ServiceError> {
    Ok(FileMetadata {
        id: parse_uuid(&get_s(item, "id")?)?,
        name: get_s(item, "name")?,
        mime_type: get_s(item, "mime_type")?,
        size_bytes: get_n_u64(item, "size_bytes")?,
        s3_key: get_s(item, "s3_key")?,
        folder_id: get_optional_s(item, "folder_id")
            .as_deref()
            .map(parse_uuid)
            .transpose()?,
        owner_id: parse_uuid(&get_s(item, "owner_id")?)?,
        version: get_n_u32(item, "version")?,
        is_trashed: get_bool(item, "is_trashed")?,
        created_at: parse_datetime(&get_s(item, "created_at")?)?,
        updated_at: parse_datetime(&get_s(item, "updated_at")?)?,
    })
}

fn parse_folder(
    item: &std::collections::HashMap<String, AttributeValue>,
) -> Result<Folder, ServiceError> {
    Ok(Folder {
        id: parse_uuid(&get_s(item, "id")?)?,
        name: get_s(item, "name")?,
        parent_id: get_optional_s(item, "parent_id")
            .as_deref()
            .map(parse_uuid)
            .transpose()?,
        owner_id: parse_uuid(&get_s(item, "owner_id")?)?,
        created_at: parse_datetime(&get_s(item, "created_at")?)?,
        updated_at: parse_datetime(&get_s(item, "updated_at")?)?,
    })
}

fn parse_file_version(
    item: &std::collections::HashMap<String, AttributeValue>,
) -> Result<FileVersion, ServiceError> {
    Ok(FileVersion {
        file_id: parse_uuid(&get_s(item, "file_id")?)?,
        version: get_n_u32(item, "version")?,
        s3_key: get_s(item, "s3_key")?,
        size_bytes: get_n_u64(item, "size_bytes")?,
        created_by: parse_uuid(&get_s(item, "created_by")?)?,
        created_at: parse_datetime(&get_s(item, "created_at")?)?,
    })
}

fn parse_file_share(
    item: &std::collections::HashMap<String, AttributeValue>,
) -> Result<FileShare, ServiceError> {
    let permission_str = get_s(item, "permission")?;
    let permission = SharePermission::from_str_value(&permission_str).ok_or_else(|| {
        ServiceError::DynamoError(format!("invalid permission: {permission_str}"))
    })?;

    Ok(FileShare {
        id: parse_uuid(&get_s(item, "id")?)?,
        file_id: parse_uuid(&get_s(item, "file_id")?)?,
        shared_with: parse_uuid(&get_s(item, "shared_with")?)?,
        permission,
        shared_by: parse_uuid(&get_s(item, "shared_by")?)?,
        created_at: parse_datetime(&get_s(item, "created_at")?)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn make_file_item() -> HashMap<String, AttributeValue> {
        let now = Utc::now();
        let id = Uuid::new_v4();
        let owner = Uuid::new_v4();
        let mut item = HashMap::new();
        item.insert("id".into(), AttributeValue::S(id.to_string()));
        item.insert("name".into(), AttributeValue::S("test.txt".into()));
        item.insert("mime_type".into(), AttributeValue::S("text/plain".into()));
        item.insert("size_bytes".into(), AttributeValue::N("1024".into()));
        item.insert("s3_key".into(), AttributeValue::S(format!("files/{id}")));
        item.insert("owner_id".into(), AttributeValue::S(owner.to_string()));
        item.insert("version".into(), AttributeValue::N("1".into()));
        item.insert("is_trashed".into(), AttributeValue::Bool(false));
        item.insert("created_at".into(), AttributeValue::S(now.to_rfc3339()));
        item.insert("updated_at".into(), AttributeValue::S(now.to_rfc3339()));
        item
    }

    #[test]
    fn test_parse_file_metadata_success() {
        let item = make_file_item();
        let result = parse_file_metadata(&item);
        assert!(result.is_ok());
        let file = result.unwrap();
        assert_eq!(file.name, "test.txt");
        assert_eq!(file.mime_type, "text/plain");
        assert_eq!(file.size_bytes, 1024);
        assert_eq!(file.version, 1);
        assert!(!file.is_trashed);
        assert!(file.folder_id.is_none());
    }

    #[test]
    fn test_parse_file_metadata_with_folder() {
        let mut item = make_file_item();
        let folder_id = Uuid::new_v4();
        item.insert("folder_id".into(), AttributeValue::S(folder_id.to_string()));
        let file = parse_file_metadata(&item).unwrap();
        assert_eq!(file.folder_id, Some(folder_id));
    }

    #[test]
    fn test_parse_file_metadata_missing_field() {
        let mut item = make_file_item();
        item.remove("name");
        let result = parse_file_metadata(&item);
        assert!(result.is_err());
    }

    #[test]
    fn test_parse_folder() {
        let now = Utc::now();
        let id = Uuid::new_v4();
        let owner = Uuid::new_v4();
        let mut item = HashMap::new();
        item.insert("id".into(), AttributeValue::S(id.to_string()));
        item.insert("name".into(), AttributeValue::S("Documents".into()));
        item.insert("owner_id".into(), AttributeValue::S(owner.to_string()));
        item.insert("created_at".into(), AttributeValue::S(now.to_rfc3339()));
        item.insert("updated_at".into(), AttributeValue::S(now.to_rfc3339()));

        let folder = parse_folder(&item).unwrap();
        assert_eq!(folder.name, "Documents");
        assert_eq!(folder.id, id);
        assert!(folder.parent_id.is_none());
    }

    #[test]
    fn test_parse_file_version() {
        let now = Utc::now();
        let file_id = Uuid::new_v4();
        let user_id = Uuid::new_v4();
        let mut item = HashMap::new();
        item.insert("file_id".into(), AttributeValue::S(file_id.to_string()));
        item.insert("version".into(), AttributeValue::N("3".into()));
        item.insert("s3_key".into(), AttributeValue::S("files/v3/key".into()));
        item.insert("size_bytes".into(), AttributeValue::N("2048".into()));
        item.insert("created_by".into(), AttributeValue::S(user_id.to_string()));
        item.insert("created_at".into(), AttributeValue::S(now.to_rfc3339()));

        let ver = parse_file_version(&item).unwrap();
        assert_eq!(ver.file_id, file_id);
        assert_eq!(ver.version, 3);
        assert_eq!(ver.size_bytes, 2048);
    }

    #[test]
    fn test_parse_file_share() {
        let now = Utc::now();
        let share_id = Uuid::new_v4();
        let file_id = Uuid::new_v4();
        let user_a = Uuid::new_v4();
        let user_b = Uuid::new_v4();
        let mut item = HashMap::new();
        item.insert("id".into(), AttributeValue::S(share_id.to_string()));
        item.insert("file_id".into(), AttributeValue::S(file_id.to_string()));
        item.insert("shared_with".into(), AttributeValue::S(user_a.to_string()));
        item.insert("permission".into(), AttributeValue::S("editor".into()));
        item.insert("shared_by".into(), AttributeValue::S(user_b.to_string()));
        item.insert("created_at".into(), AttributeValue::S(now.to_rfc3339()));

        let share = parse_file_share(&item).unwrap();
        assert_eq!(share.permission, SharePermission::Editor);
        assert_eq!(share.file_id, file_id);
    }

    #[test]
    fn test_share_permission_from_str() {
        assert_eq!(
            SharePermission::from_str_value("viewer"),
            Some(SharePermission::Viewer)
        );
        assert_eq!(
            SharePermission::from_str_value("Editor"),
            Some(SharePermission::Editor)
        );
        assert_eq!(SharePermission::from_str_value("invalid"), None);
    }
}

/// Behavioral tests for the metadata layer, driven against an in-process fake
/// DynamoDB (no Docker, no LocalStack, no network beyond loopback).
#[cfg(test)]
mod behavior_tests {
    use super::*;
    use crate::models::SharePermission;

    /// A minimal, in-process implementation of the DynamoDB JSON wire protocol
    /// — enough of `PutItem` / `GetItem` / `UpdateItem` / `DeleteItem` /
    /// `Scan` / `Query` to exercise `MetadataClient` end to end. Each test gets
    /// its own server and its own tables, so no state is shared between tests.
    mod fake_dynamo {
        use serde_json::{json, Map, Value};
        use std::collections::HashMap;
        use std::net::SocketAddr;
        use std::sync::{Arc, Mutex};
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::{TcpListener, TcpStream};

        pub type Item = Map<String, Value>;

        #[derive(Default)]
        pub struct Tables {
            data: HashMap<String, Vec<Item>>,
            failing: bool,
        }

        pub struct FakeDynamo {
            pub addr: SocketAddr,
            state: Arc<Mutex<Tables>>,
        }

        pub async fn start() -> FakeDynamo {
            let listener = TcpListener::bind("127.0.0.1:0")
                .await
                .expect("fake dynamo binds a loopback port");
            let addr = listener.local_addr().unwrap();
            let state = Arc::new(Mutex::new(Tables::default()));
            let server_state = Arc::clone(&state);

            tokio::spawn(async move {
                while let Ok((stream, _)) = listener.accept().await {
                    let connection_state = Arc::clone(&server_state);
                    tokio::spawn(serve_connection(stream, connection_state));
                }
            });

            FakeDynamo { addr, state }
        }

        impl FakeDynamo {
            pub fn endpoint(&self) -> String {
                format!("http://{}", self.addr)
            }

            /// Writes an item straight into a table, bypassing `MetadataClient`
            /// — used to plant rows that the service itself could not create.
            pub fn raw_put(&self, table: &str, item: Item) {
                let mut state = self.state.lock().unwrap();
                state.data.entry(table.to_string()).or_default().push(item);
            }

            /// Makes every subsequent request fail with a DynamoDB 500, so a
            /// test can exercise the transport-error branches.
            pub fn set_failing(&self, failing: bool) {
                self.state.lock().unwrap().failing = failing;
            }

            pub fn row_count(&self, table: &str) -> usize {
                let state = self.state.lock().unwrap();
                state.data.get(table).map(|rows| rows.len()).unwrap_or(0)
            }
        }

        fn key_attrs(table: &str) -> &'static [&'static str] {
            if table.contains("version") {
                &["file_id", "version"]
            } else {
                &["id"]
            }
        }

        fn matches_key(item: &Item, key: &Map<String, Value>) -> bool {
            key.iter().all(|(k, v)| item.get(k) == Some(v))
        }

        fn resolve_name(raw: &str, names: &Map<String, Value>) -> String {
            let raw = raw.trim();
            if raw.starts_with('#') {
                names
                    .get(raw)
                    .and_then(|v| v.as_str())
                    .unwrap_or(raw)
                    .to_string()
            } else {
                raw.to_string()
            }
        }

        fn condition_holds(condition: Option<&str>, existing: Option<&Item>) -> bool {
            let Some(condition) = condition else {
                return true;
            };
            let condition = condition.trim();
            if let Some(attr) = condition
                .strip_prefix("attribute_exists(")
                .and_then(|rest| rest.strip_suffix(')'))
            {
                return existing
                    .map(|item| item.contains_key(attr.trim()))
                    .unwrap_or(false);
            }
            if let Some(attr) = condition
                .strip_prefix("attribute_not_exists(")
                .and_then(|rest| rest.strip_suffix(')'))
            {
                return existing
                    .map(|item| !item.contains_key(attr.trim()))
                    .unwrap_or(true);
            }
            panic!("fake dynamo does not implement condition {condition:?}");
        }

        fn filter_matches(filter: Option<&str>, item: &Item, values: &Map<String, Value>) -> bool {
            let Some(filter) = filter else {
                return true;
            };
            filter.split(" AND ").all(|term| {
                let term = term.trim();
                if let Some(attr) = term
                    .strip_prefix("attribute_not_exists(")
                    .and_then(|rest| rest.strip_suffix(')'))
                {
                    return !item.contains_key(attr.trim());
                }
                if let Some(attr) = term
                    .strip_prefix("attribute_exists(")
                    .and_then(|rest| rest.strip_suffix(')'))
                {
                    return item.contains_key(attr.trim());
                }
                let (lhs, rhs) = term
                    .split_once('=')
                    .unwrap_or_else(|| panic!("fake dynamo does not implement filter {term:?}"));
                match values.get(rhs.trim()) {
                    Some(expected) => item.get(lhs.trim()) == Some(expected),
                    None => false,
                }
            })
        }

        fn apply_update(
            item: &mut Item,
            expression: &str,
            names: &Map<String, Value>,
            values: &Map<String, Value>,
        ) {
            let (set_clause, remove_clause) = match expression.find(" REMOVE ") {
                Some(index) => (
                    &expression[..index],
                    Some(expression[index + " REMOVE ".len()..].trim()),
                ),
                None => (expression, None),
            };

            if let Some(assignments) = set_clause.trim().strip_prefix("SET ") {
                for assignment in assignments.split(',') {
                    let (lhs, rhs) = assignment
                        .split_once('=')
                        .expect("SET clause is `name = :value`");
                    let name = resolve_name(lhs, names);
                    let value = values
                        .get(rhs.trim())
                        .unwrap_or_else(|| panic!("missing expression value {rhs:?}"))
                        .clone();
                    item.insert(name, value);
                }
            }

            if let Some(removals) = remove_clause {
                for removal in removals.split(',') {
                    item.remove(&resolve_name(removal, names));
                }
            }
        }

        fn object(body: &Value, key: &str) -> Map<String, Value> {
            body.get(key)
                .and_then(|v| v.as_object())
                .cloned()
                .unwrap_or_default()
        }

        fn handle(target: &str, body: &Value, state: &Arc<Mutex<Tables>>) -> (u16, Value) {
            let operation = target.rsplit('.').next().unwrap_or_default();
            let table = body
                .get("TableName")
                .and_then(|v| v.as_str())
                .unwrap_or_default()
                .to_string();
            let values = object(body, "ExpressionAttributeValues");
            let names = object(body, "ExpressionAttributeNames");
            let mut state = state.lock().unwrap();
            if state.failing {
                return (
                    500,
                    json!({
                        "__type": "com.amazonaws.dynamodb.v20120810#InternalServerError",
                        "message": "fake dynamo fault injection",
                    }),
                );
            }
            let rows = state.data.entry(table.clone()).or_default();

            match operation {
                "PutItem" => {
                    let item = object(body, "Item");
                    let key: Map<String, Value> = key_attrs(&table)
                        .iter()
                        .filter_map(|attr| {
                            item.get(*attr).map(|v| ((*attr).to_string(), v.clone()))
                        })
                        .collect();
                    rows.retain(|existing| !matches_key(existing, &key));
                    rows.push(item);
                    (200, json!({}))
                }
                "GetItem" => {
                    let key = object(body, "Key");
                    match rows.iter().find(|item| matches_key(item, &key)) {
                        Some(item) => (200, json!({ "Item": item })),
                        None => (200, json!({})),
                    }
                }
                "DeleteItem" => {
                    let key = object(body, "Key");
                    rows.retain(|item| !matches_key(item, &key));
                    (200, json!({}))
                }
                "UpdateItem" => {
                    let key = object(body, "Key");
                    let condition = body.get("ConditionExpression").and_then(|v| v.as_str());
                    let position = rows.iter().position(|item| matches_key(item, &key));
                    let existing = position.map(|index| &rows[index]);
                    if !condition_holds(condition, existing) {
                        return (
                            400,
                            json!({
                                "__type": "com.amazonaws.dynamodb.v20120810#ConditionalCheckFailedException",
                                "message": "The conditional request failed",
                            }),
                        );
                    }
                    let expression = body
                        .get("UpdateExpression")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string();
                    match position {
                        Some(index) => apply_update(&mut rows[index], &expression, &names, &values),
                        None => {
                            let mut item = key.clone();
                            apply_update(&mut item, &expression, &names, &values);
                            rows.push(item);
                        }
                    }
                    (200, json!({}))
                }
                "Scan" => {
                    let filter = body.get("FilterExpression").and_then(|v| v.as_str());
                    let items: Vec<&Item> = rows
                        .iter()
                        .filter(|item| filter_matches(filter, item, &values))
                        .collect();
                    (200, json!({ "Items": items, "Count": items.len() }))
                }
                "Query" => {
                    let condition = body.get("KeyConditionExpression").and_then(|v| v.as_str());
                    let mut items: Vec<Item> = rows
                        .iter()
                        .filter(|item| filter_matches(condition, item, &values))
                        .cloned()
                        .collect();
                    let sort_key = key_attrs(&table).last().copied().unwrap_or("id");
                    items.sort_by_key(|item| {
                        item.get(sort_key)
                            .and_then(|v| v.get("N"))
                            .and_then(|v| v.as_str())
                            .and_then(|n| n.parse::<i64>().ok())
                            .unwrap_or(0)
                    });
                    if body.get("ScanIndexForward") == Some(&Value::Bool(false)) {
                        items.reverse();
                    }
                    (200, json!({ "Items": items, "Count": items.len() }))
                }
                other => (
                    400,
                    json!({
                        "__type": "com.amazonaws.dynamodb.v20120810#UnknownOperationException",
                        "message": format!("fake dynamo does not implement {other}"),
                    }),
                ),
            }
        }

        async fn serve_connection(mut stream: TcpStream, state: Arc<Mutex<Tables>>) {
            let mut buffer: Vec<u8> = Vec::new();
            loop {
                let header_end = loop {
                    if let Some(position) = buffer
                        .windows(4)
                        .position(|window| window == b"\r\n\r\n")
                        .map(|position| position + 4)
                    {
                        break position;
                    }
                    let mut chunk = [0u8; 4096];
                    match stream.read(&mut chunk).await {
                        Ok(0) | Err(_) => return,
                        Ok(read) => buffer.extend_from_slice(&chunk[..read]),
                    }
                };

                let head = String::from_utf8_lossy(&buffer[..header_end]).to_string();
                let mut content_length = 0usize;
                let mut target = String::new();
                for line in head.lines().skip(1) {
                    if let Some((name, value)) = line.split_once(':') {
                        match name.trim().to_ascii_lowercase().as_str() {
                            "content-length" => content_length = value.trim().parse().unwrap_or(0),
                            "x-amz-target" => target = value.trim().to_string(),
                            _ => {}
                        }
                    }
                }

                while buffer.len() < header_end + content_length {
                    let mut chunk = [0u8; 4096];
                    match stream.read(&mut chunk).await {
                        Ok(0) | Err(_) => return,
                        Ok(read) => buffer.extend_from_slice(&chunk[..read]),
                    }
                }

                let request: Value =
                    serde_json::from_slice(&buffer[header_end..header_end + content_length])
                        .unwrap_or_else(|_| json!({}));
                buffer.drain(..header_end + content_length);

                let (status, payload) = handle(&target, &request, &state);
                let body = payload.to_string();
                let status_line = match status {
                    200 => "200 OK",
                    500 => "500 Internal Server Error",
                    _ => "400 Bad Request",
                };
                let error_type = if status == 200 {
                    String::new()
                } else {
                    format!(
                        "x-amzn-errortype: {}\r\n",
                        payload["__type"]
                            .as_str()
                            .unwrap_or("UnknownError")
                            .rsplit('#')
                            .next()
                            .unwrap_or("UnknownError")
                    )
                };
                let response = format!(
                    "HTTP/1.1 {status_line}\r\n\
                     content-type: application/x-amz-json-1.0\r\n\
                     x-amzn-requestid: 00000000-0000-0000-0000-000000000000\r\n\
                     {error_type}content-length: {}\r\n\r\n{body}",
                    body.len()
                );
                if stream.write_all(response.as_bytes()).await.is_err() {
                    return;
                }
            }
        }
    }

    use fake_dynamo::FakeDynamo;

    const FILES: &str = "test-files";
    const FOLDERS: &str = "test-folders";
    const VERSIONS: &str = "test-file-versions";
    const SHARES: &str = "test-file-shares";

    /// A fixed instant, so nothing in these tests depends on the wall clock.
    fn at(seconds: i64) -> chrono::DateTime<Utc> {
        chrono::DateTime::from_timestamp(1_700_000_000 + seconds, 0).expect("valid timestamp")
    }

    struct Harness {
        dynamo: FakeDynamo,
        meta: MetadataClient,
    }

    async fn harness() -> Harness {
        let dynamo = fake_dynamo::start().await;
        let config = aws_sdk_dynamodb::Config::builder()
            .behavior_version(aws_sdk_dynamodb::config::BehaviorVersion::latest())
            .region(aws_sdk_dynamodb::config::Region::new("us-east-1"))
            .credentials_provider(aws_sdk_dynamodb::config::Credentials::new(
                "fake",
                "fake",
                None,
                None,
                "fake-dynamo",
            ))
            .endpoint_url(dynamo.endpoint())
            .retry_config(aws_sdk_dynamodb::config::retry::RetryConfig::disabled())
            .build();

        let meta = MetadataClient {
            client: aws_sdk_dynamodb::Client::from_conf(config),
            files_table: FILES.to_string(),
            folders_table: FOLDERS.to_string(),
            versions_table: VERSIONS.to_string(),
            shares_table: SHARES.to_string(),
        };

        Harness { dynamo, meta }
    }

    fn file_owned_by(owner: Uuid, name: &str, folder: Option<Uuid>) -> FileMetadata {
        let id = Uuid::new_v4();
        FileMetadata {
            id,
            name: name.to_string(),
            mime_type: "text/plain".into(),
            size_bytes: 12,
            s3_key: format!("files/{owner}/{id}"),
            folder_id: folder,
            owner_id: owner,
            version: 1,
            is_trashed: false,
            created_at: at(0),
            updated_at: at(0),
        }
    }

    fn folder_owned_by(owner: Uuid, name: &str, parent: Option<Uuid>) -> Folder {
        Folder {
            id: Uuid::new_v4(),
            name: name.to_string(),
            parent_id: parent,
            owner_id: owner,
            created_at: at(0),
            updated_at: at(0),
        }
    }

    fn share(file: Uuid, owner: Uuid, with: Uuid, permission: SharePermission) -> FileShare {
        FileShare {
            id: Uuid::new_v4(),
            file_id: file,
            shared_with: with,
            permission,
            shared_by: owner,
            created_at: at(0),
        }
    }

    /// Walks a folder's ancestry, stopping if it revisits a folder. Returns the
    /// chain and whether a cycle was detected.
    async fn ancestry(meta: &MetadataClient, start: Uuid) -> (Vec<Uuid>, bool) {
        let mut chain = vec![start];
        let mut current = start;
        for _ in 0..16 {
            let Ok(folder) = meta.get_folder(&current).await else {
                return (chain, false);
            };
            let Some(parent) = folder.parent_id else {
                return (chain, false);
            };
            if chain.contains(&parent) {
                chain.push(parent);
                return (chain, true);
            }
            chain.push(parent);
            current = parent;
        }
        (chain, true)
    }

    // ── Harness sanity ────────────────────────────────────────────────────

    #[tokio::test]
    async fn round_trips_a_file_through_the_metadata_client() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let file = file_owned_by(owner, "notes.txt", None);

        h.meta.put_file(&file).await.unwrap();
        let fetched = h.meta.get_file(&file.id).await.unwrap();

        assert_eq!(fetched.id, file.id);
        assert_eq!(fetched.name, "notes.txt");
        assert_eq!(fetched.owner_id, owner);
        assert!(!fetched.is_trashed);
        assert_eq!(h.dynamo.row_count(FILES), 1);
    }

    #[tokio::test]
    async fn get_file_reports_not_found_for_an_unknown_id() {
        let h = harness().await;
        let missing = Uuid::new_v4();
        match h.meta.get_file(&missing).await {
            Err(ServiceError::FileNotFound(id)) => assert_eq!(id, missing.to_string()),
            other => panic!("expected FileNotFound, got {other:?}"),
        }
    }

    #[tokio::test]
    async fn put_file_is_an_upsert_rather_than_a_duplicate_insert() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let mut file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();
        file.name = "b.txt".into();
        h.meta.put_file(&file).await.unwrap();

        assert_eq!(h.dynamo.row_count(FILES), 1);
        assert_eq!(h.meta.get_file(&file.id).await.unwrap().name, "b.txt");
    }

    // ── Trash / restore ───────────────────────────────────────────────────

    #[tokio::test]
    async fn trash_marks_the_file_and_leaves_it_readable() {
        let h = harness().await;
        let file = file_owned_by(Uuid::new_v4(), "a.txt", None);
        h.meta.put_file(&file).await.unwrap();

        let trashed = h.meta.trash_file(&file.id).await.unwrap();
        assert!(trashed.is_trashed);
        assert!(h.meta.get_file(&file.id).await.unwrap().is_trashed);
    }

    #[tokio::test]
    async fn trashing_twice_is_idempotent() {
        let h = harness().await;
        let file = file_owned_by(Uuid::new_v4(), "a.txt", None);
        h.meta.put_file(&file).await.unwrap();

        let first = h.meta.trash_file(&file.id).await.unwrap();
        let second = h.meta.trash_file(&file.id).await.unwrap();

        assert!(first.is_trashed && second.is_trashed);
        assert_eq!(first.id, second.id);
        assert_eq!(h.dynamo.row_count(FILES), 1);
        assert_eq!(h.meta.list_trashed(None).await.unwrap().len(), 1);
    }

    #[tokio::test]
    async fn trashing_an_unknown_file_is_a_not_found_not_an_upsert() {
        let h = harness().await;
        let missing = Uuid::new_v4();
        match h.meta.trash_file(&missing).await {
            Err(ServiceError::FileNotFound(id)) => assert_eq!(id, missing.to_string()),
            other => panic!("expected FileNotFound, got {other:?}"),
        }
        assert_eq!(
            h.dynamo.row_count(FILES),
            0,
            "a failed conditional update must not create a row"
        );
    }

    #[tokio::test]
    async fn restoring_a_file_that_was_never_trashed_succeeds_as_a_no_op() {
        let h = harness().await;
        let file = file_owned_by(Uuid::new_v4(), "a.txt", None);
        h.meta.put_file(&file).await.unwrap();

        let restored = h.meta.restore_file(&file.id).await.unwrap();
        assert!(!restored.is_trashed);
        assert!(h.meta.list_trashed(None).await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn restoring_twice_is_idempotent() {
        let h = harness().await;
        let file = file_owned_by(Uuid::new_v4(), "a.txt", None);
        h.meta.put_file(&file).await.unwrap();
        h.meta.trash_file(&file.id).await.unwrap();

        assert!(!h.meta.restore_file(&file.id).await.unwrap().is_trashed);
        assert!(!h.meta.restore_file(&file.id).await.unwrap().is_trashed);
        assert!(h.meta.list_trashed(None).await.unwrap().is_empty());
    }

    #[tokio::test]
    async fn restoring_an_unknown_file_is_a_not_found() {
        let h = harness().await;
        let missing = Uuid::new_v4();
        assert!(matches!(
            h.meta.restore_file(&missing).await,
            Err(ServiceError::FileNotFound(_))
        ));
    }

    #[tokio::test]
    async fn restoring_a_hard_deleted_file_is_a_not_found() {
        let h = harness().await;
        let file = file_owned_by(Uuid::new_v4(), "a.txt", None);
        h.meta.put_file(&file).await.unwrap();
        h.meta.trash_file(&file.id).await.unwrap();
        h.meta.delete_file(&file.id).await.unwrap();

        assert!(matches!(
            h.meta.restore_file(&file.id).await,
            Err(ServiceError::FileNotFound(_))
        ));
    }

    #[tokio::test]
    async fn deleting_an_unknown_file_reports_success() {
        // DynamoDB DeleteItem is unconditional here, so a repeated delete is
        // silently accepted; the handler is what returns 404, by reading first.
        let h = harness().await;
        h.meta.delete_file(&Uuid::new_v4()).await.unwrap();
        h.meta.delete_file(&Uuid::new_v4()).await.unwrap();
        assert_eq!(h.dynamo.row_count(FILES), 0);
    }

    #[tokio::test]
    async fn restoring_into_a_deleted_parent_folder_leaves_an_unreachable_file() {
        // Documents current behavior. See FINDING in
        // restoring_into_a_deleted_parent_should_not_strand_the_file.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let folder = folder_owned_by(owner, "Reports", None);
        let file = file_owned_by(owner, "q3.pdf", Some(folder.id));
        h.meta.put_folder(&folder).await.unwrap();
        h.meta.put_file(&file).await.unwrap();

        h.meta.trash_file(&file.id).await.unwrap();
        h.meta.delete_folder(&folder.id).await.unwrap();
        let restored = h.meta.restore_file(&file.id).await.unwrap();

        assert!(!restored.is_trashed);
        assert_eq!(restored.folder_id, Some(folder.id));
        assert!(matches!(
            h.meta.get_folder(&folder.id).await,
            Err(ServiceError::FolderNotFound(_))
        ));
        // The file is not in the trash, not at the root, and its folder is gone.
        let at_root = h.meta.list_files(None, Some(owner), false).await.unwrap();
        assert!(at_root.iter().any(|f| f.id == file.id));
        assert!(h
            .meta
            .list_folders(None, Some(owner))
            .await
            .unwrap()
            .is_empty());
    }

    #[tokio::test]
    #[ignore = "FINDING: restoring a file whose parent folder was deleted while it sat in the \
                trash leaves the file pointing at a folder id that no longer exists. Nothing \
                reparents it to the root, so it is invisible in any folder listing yet still \
                counts against the owner's storage"]
    async fn restoring_into_a_deleted_parent_should_not_strand_the_file() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let folder = folder_owned_by(owner, "Reports", None);
        let file = file_owned_by(owner, "q3.pdf", Some(folder.id));
        h.meta.put_folder(&folder).await.unwrap();
        h.meta.put_file(&file).await.unwrap();
        h.meta.trash_file(&file.id).await.unwrap();
        h.meta.delete_folder(&folder.id).await.unwrap();

        let restored = h.meta.restore_file(&file.id).await.unwrap();
        assert_eq!(
            restored.folder_id, None,
            "a restored file must land somewhere reachable"
        );
    }

    #[tokio::test]
    async fn trashed_files_are_excluded_from_listings_unless_explicitly_included() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let kept = file_owned_by(owner, "kept.txt", None);
        let binned = file_owned_by(owner, "binned.txt", None);
        h.meta.put_file(&kept).await.unwrap();
        h.meta.put_file(&binned).await.unwrap();
        h.meta.trash_file(&binned.id).await.unwrap();

        let visible = h.meta.list_files(None, Some(owner), false).await.unwrap();
        assert_eq!(visible.len(), 1);
        assert_eq!(visible[0].id, kept.id);

        let everything = h.meta.list_files(None, Some(owner), true).await.unwrap();
        assert_eq!(everything.len(), 2);

        let trash = h.meta.list_trashed(Some(owner)).await.unwrap();
        assert_eq!(trash.len(), 1);
        assert_eq!(trash[0].id, binned.id);
    }

    #[tokio::test]
    async fn list_trashed_is_scoped_to_the_requesting_owner() {
        let h = harness().await;
        let alice = Uuid::new_v4();
        let bob = Uuid::new_v4();
        let alices = file_owned_by(alice, "a.txt", None);
        let bobs = file_owned_by(bob, "b.txt", None);
        h.meta.put_file(&alices).await.unwrap();
        h.meta.put_file(&bobs).await.unwrap();
        h.meta.trash_file(&alices.id).await.unwrap();
        h.meta.trash_file(&bobs.id).await.unwrap();

        let alice_trash = h.meta.list_trashed(Some(alice)).await.unwrap();
        assert_eq!(alice_trash.len(), 1);
        assert_eq!(alice_trash[0].owner_id, alice);
        assert!(
            !alice_trash.iter().any(|f| f.id == bobs.id),
            "one owner's trash must never contain another owner's file"
        );

        assert_eq!(h.meta.list_trashed(None).await.unwrap().len(), 2);
    }

    #[tokio::test]
    async fn list_trashed_orders_by_most_recently_updated() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let mut older = file_owned_by(owner, "older.txt", None);
        older.is_trashed = true;
        older.updated_at = at(10);
        let mut newer = file_owned_by(owner, "newer.txt", None);
        newer.is_trashed = true;
        newer.updated_at = at(20);
        h.meta.put_file(&older).await.unwrap();
        h.meta.put_file(&newer).await.unwrap();

        let trash = h.meta.list_trashed(Some(owner)).await.unwrap();
        assert_eq!(
            trash.iter().map(|f| f.name.as_str()).collect::<Vec<_>>(),
            vec!["newer.txt", "older.txt"]
        );
    }

    // ── Files: rename / move, and the missing owner check ─────────────────

    #[tokio::test]
    async fn rename_and_move_update_only_the_targeted_file() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let folder = folder_owned_by(owner, "Docs", None);
        let file = file_owned_by(owner, "old.txt", None);
        let bystander = file_owned_by(owner, "other.txt", None);
        h.meta.put_folder(&folder).await.unwrap();
        h.meta.put_file(&file).await.unwrap();
        h.meta.put_file(&bystander).await.unwrap();

        let renamed = h.meta.rename_file(&file.id, "new.txt").await.unwrap();
        assert_eq!(renamed.name, "new.txt");

        let moved = h.meta.move_file(&file.id, Some(folder.id)).await.unwrap();
        assert_eq!(moved.folder_id, Some(folder.id));

        let back_to_root = h.meta.move_file(&file.id, None).await.unwrap();
        assert_eq!(back_to_root.folder_id, None, "None clears the folder");

        let untouched = h.meta.get_file(&bystander.id).await.unwrap();
        assert_eq!(untouched.name, "other.txt");
        assert_eq!(untouched.folder_id, None);
    }

    #[tokio::test]
    async fn renaming_or_moving_an_unknown_file_is_a_not_found() {
        let h = harness().await;
        let missing = Uuid::new_v4();
        assert!(matches!(
            h.meta.rename_file(&missing, "x").await,
            Err(ServiceError::FileNotFound(_))
        ));
        assert!(matches!(
            h.meta.move_file(&missing, None).await,
            Err(ServiceError::FileNotFound(_))
        ));
        assert_eq!(h.dynamo.row_count(FILES), 0);
    }

    #[tokio::test]
    async fn a_file_can_be_moved_into_a_folder_owned_by_someone_else() {
        // Documents current behavior. See FINDING in
        // moving_a_file_into_another_owners_folder_should_be_refused.
        let h = harness().await;
        let alice = Uuid::new_v4();
        let bob = Uuid::new_v4();
        let bobs_folder = folder_owned_by(bob, "Bob private", None);
        let alices_file = file_owned_by(alice, "a.txt", None);
        h.meta.put_folder(&bobs_folder).await.unwrap();
        h.meta.put_file(&alices_file).await.unwrap();

        let moved = h
            .meta
            .move_file(&alices_file.id, Some(bobs_folder.id))
            .await
            .unwrap();
        assert_eq!(moved.folder_id, Some(bobs_folder.id));
        assert_eq!(moved.owner_id, alice);

        let bobs_view = h
            .meta
            .list_files(Some(bobs_folder.id), Some(bob), false)
            .await
            .unwrap();
        assert!(
            bobs_view.is_empty(),
            "the owner filter still hides it, but the row now lives in Bob's tree"
        );
    }

    #[tokio::test]
    #[ignore = "FINDING: no layer checks that the destination folder belongs to the same owner \
                as the file (metadata takes no actor at all and the handler does not look), so a \
                file can be filed into another tenant's folder"]
    async fn moving_a_file_into_another_owners_folder_should_be_refused() {
        let h = harness().await;
        let alice = Uuid::new_v4();
        let bob = Uuid::new_v4();
        let bobs_folder = folder_owned_by(bob, "Bob private", None);
        let alices_file = file_owned_by(alice, "a.txt", None);
        h.meta.put_folder(&bobs_folder).await.unwrap();
        h.meta.put_file(&alices_file).await.unwrap();

        assert!(
            h.meta
                .move_file(&alices_file.id, Some(bobs_folder.id))
                .await
                .is_err(),
            "cross-owner move must be rejected"
        );
    }

    #[tokio::test]
    async fn moving_a_file_into_a_folder_that_does_not_exist_is_accepted() {
        // Documents current behavior: there is no referential integrity check.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();

        let ghost = Uuid::new_v4();
        let moved = h.meta.move_file(&file.id, Some(ghost)).await.unwrap();
        assert_eq!(moved.folder_id, Some(ghost));
        assert!(matches!(
            h.meta.get_folder(&ghost).await,
            Err(ServiceError::FolderNotFound(_))
        ));
    }

    #[tokio::test]
    async fn list_files_filters_by_folder_and_owner_independently() {
        let h = harness().await;
        let alice = Uuid::new_v4();
        let bob = Uuid::new_v4();
        let folder = folder_owned_by(alice, "Shared", None);
        h.meta.put_folder(&folder).await.unwrap();
        let alice_in_folder = file_owned_by(alice, "a-in.txt", Some(folder.id));
        let alice_at_root = file_owned_by(alice, "a-root.txt", None);
        let bob_in_folder = file_owned_by(bob, "b-in.txt", Some(folder.id));
        for file in [&alice_in_folder, &alice_at_root, &bob_in_folder] {
            h.meta.put_file(file).await.unwrap();
        }

        let in_folder = h
            .meta
            .list_files(Some(folder.id), None, false)
            .await
            .unwrap();
        assert_eq!(in_folder.len(), 2);

        let alices = h.meta.list_files(None, Some(alice), false).await.unwrap();
        assert_eq!(alices.len(), 2);

        let alices_in_folder = h
            .meta
            .list_files(Some(folder.id), Some(alice), false)
            .await
            .unwrap();
        assert_eq!(alices_in_folder.len(), 1);
        assert_eq!(alices_in_folder[0].id, alice_in_folder.id);

        let nobody = h
            .meta
            .list_files(None, Some(Uuid::new_v4()), false)
            .await
            .unwrap();
        assert!(nobody.is_empty());
    }

    #[tokio::test]
    async fn the_metadata_layer_accepts_a_mutation_from_any_caller() {
        // Documents current behavior: every mutating method is keyed only by
        // resource id — there is no actor argument, so ownership cannot be
        // enforced here. See FINDING in
        // mutations_should_be_refused_for_a_non_owner.
        let h = harness().await;
        let alice = Uuid::new_v4();
        let file = file_owned_by(alice, "alice.txt", None);
        h.meta.put_file(&file).await.unwrap();

        assert!(h.meta.rename_file(&file.id, "taken.txt").await.is_ok());
        assert!(h.meta.trash_file(&file.id).await.is_ok());
        assert!(h.meta.restore_file(&file.id).await.is_ok());
        assert!(h.meta.delete_file(&file.id).await.is_ok());
    }

    #[tokio::test]
    #[ignore = "FINDING: no file mutation is authorized anywhere in the service. MetadataClient \
                takes no actor and the handlers never compare X-User-ID with FileMetadata::\
                owner_id, so any caller who knows a file id can rename, move, trash or delete \
                another user's file"]
    async fn mutations_should_be_refused_for_a_non_owner() {
        let h = harness().await;
        let alice = Uuid::new_v4();
        let file = file_owned_by(alice, "alice.txt", None);
        h.meta.put_file(&file).await.unwrap();

        // Bob is not the owner and has no share; this must not succeed.
        assert!(h.meta.rename_file(&file.id, "taken.txt").await.is_err());
    }

    // ── Folders ───────────────────────────────────────────────────────────

    #[tokio::test]
    async fn folders_round_trip_and_list_by_parent_and_owner() {
        let h = harness().await;
        let alice = Uuid::new_v4();
        let bob = Uuid::new_v4();
        let root = folder_owned_by(alice, "Root", None);
        let child = folder_owned_by(alice, "Child", Some(root.id));
        let bobs_root = folder_owned_by(bob, "Bob root", None);
        for folder in [&root, &child, &bobs_root] {
            h.meta.put_folder(folder).await.unwrap();
        }

        let alices_roots = h.meta.list_folders(None, Some(alice)).await.unwrap();
        assert_eq!(alices_roots.len(), 1);
        assert_eq!(alices_roots[0].id, root.id);

        let children = h.meta.list_folders(Some(root.id), None).await.unwrap();
        assert_eq!(children.len(), 1);
        assert_eq!(children[0].id, child.id);

        let all_roots = h.meta.list_folders(None, None).await.unwrap();
        assert_eq!(
            all_roots.len(),
            2,
            "root listing spans owners when unfiltered"
        );

        assert!(matches!(
            h.meta.get_folder(&Uuid::new_v4()).await,
            Err(ServiceError::FolderNotFound(_))
        ));
    }

    #[tokio::test]
    async fn updating_an_unknown_folder_is_a_not_found() {
        let h = harness().await;
        let missing = Uuid::new_v4();
        assert!(matches!(
            h.meta.update_folder(&missing, Some("x".into()), None).await,
            Err(ServiceError::FolderNotFound(_))
        ));
        assert_eq!(h.dynamo.row_count(FOLDERS), 0);
    }

    #[tokio::test]
    async fn a_folder_can_be_renamed_to_a_name_a_sibling_already_uses() {
        // Documents current behavior: sibling names are not unique, so a tree
        // can hold two identically named folders under the same parent.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let parent = folder_owned_by(owner, "Parent", None);
        let first = folder_owned_by(owner, "Reports", Some(parent.id));
        let second = folder_owned_by(owner, "Drafts", Some(parent.id));
        for folder in [&parent, &first, &second] {
            h.meta.put_folder(folder).await.unwrap();
        }

        let renamed = h
            .meta
            .update_folder(&second.id, Some("Reports".into()), None)
            .await
            .unwrap();
        assert_eq!(renamed.name, "Reports");

        let siblings = h.meta.list_folders(Some(parent.id), None).await.unwrap();
        assert_eq!(siblings.len(), 2);
        assert_eq!(
            siblings.iter().filter(|f| f.name == "Reports").count(),
            2,
            "two sibling folders now share a name"
        );
    }

    #[tokio::test]
    async fn a_folder_can_be_renamed_to_a_blank_name() {
        let h = harness().await;
        let folder = folder_owned_by(Uuid::new_v4(), "Named", None);
        h.meta.put_folder(&folder).await.unwrap();

        let renamed = h
            .meta
            .update_folder(&folder.id, Some("   ".into()), None)
            .await
            .unwrap();
        assert_eq!(
            renamed.name, "   ",
            "folders accept a blank name even though files do not"
        );
    }

    #[tokio::test]
    async fn a_folder_cannot_be_moved_back_to_the_root() {
        // `update_folder` only ever SETs parent_id; passing None means "leave
        // it alone", so there is no way to unparent a folder.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let parent = folder_owned_by(owner, "Parent", None);
        let child = folder_owned_by(owner, "Child", Some(parent.id));
        h.meta.put_folder(&parent).await.unwrap();
        h.meta.put_folder(&child).await.unwrap();

        let unchanged = h
            .meta
            .update_folder(&child.id, Some("Renamed".into()), None)
            .await
            .unwrap();
        assert_eq!(unchanged.parent_id, Some(parent.id));
        assert!(h
            .meta
            .list_folders(None, Some(owner))
            .await
            .unwrap()
            .iter()
            .all(|f| f.id != child.id));
    }

    #[tokio::test]
    #[ignore = "FINDING: update_folder cannot clear parent_id (None means 'unchanged' and the \
                REMOVE branch that move_file has does not exist for folders), so once a folder \
                is nested it can never be moved back to the root"]
    async fn a_folder_should_be_movable_back_to_the_root() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let parent = folder_owned_by(owner, "Parent", None);
        let child = folder_owned_by(owner, "Child", Some(parent.id));
        h.meta.put_folder(&parent).await.unwrap();
        h.meta.put_folder(&child).await.unwrap();

        let moved = h.meta.update_folder(&child.id, None, None).await.unwrap();
        assert_eq!(moved.parent_id, None);
    }

    #[tokio::test]
    async fn a_folder_can_be_made_its_own_parent() {
        // Documents current behavior. See FINDING in
        // folder_cycles_should_be_rejected.
        let h = harness().await;
        let folder = folder_owned_by(Uuid::new_v4(), "Loop", None);
        h.meta.put_folder(&folder).await.unwrap();

        let updated = h
            .meta
            .update_folder(&folder.id, None, Some(folder.id))
            .await
            .unwrap();
        assert_eq!(updated.parent_id, Some(folder.id));

        let (chain, cycled) = ancestry(&h.meta, folder.id).await;
        assert!(cycled, "self-parenting is an immediate cycle: {chain:?}");
    }

    #[tokio::test]
    async fn a_folder_can_be_moved_into_its_own_descendant() {
        // Documents current behavior: grandparent -> child of its own
        // grandchild, which detaches the whole branch from every root listing.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let grandparent = folder_owned_by(owner, "A", None);
        let parent = folder_owned_by(owner, "B", Some(grandparent.id));
        let child = folder_owned_by(owner, "C", Some(parent.id));
        for folder in [&grandparent, &parent, &child] {
            h.meta.put_folder(folder).await.unwrap();
        }

        let updated = h
            .meta
            .update_folder(&grandparent.id, None, Some(child.id))
            .await
            .unwrap();
        assert_eq!(updated.parent_id, Some(child.id));

        let (chain, cycled) = ancestry(&h.meta, child.id).await;
        assert!(cycled, "expected a cycle, walked {chain:?}");

        // Every folder in the cycle has a parent, so the owner's root listing
        // is now empty and the whole branch is unreachable from the UI.
        assert!(h
            .meta
            .list_folders(None, Some(owner))
            .await
            .unwrap()
            .is_empty());
    }

    #[tokio::test]
    #[ignore = "FINDING: update_folder performs no cycle check. Moving a folder into its own \
                descendant (or into itself) creates a parent cycle; the branch disappears from \
                the root listing and any client that walks the ancestry loops forever"]
    async fn folder_cycles_should_be_rejected() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let parent = folder_owned_by(owner, "A", None);
        let child = folder_owned_by(owner, "B", Some(parent.id));
        h.meta.put_folder(&parent).await.unwrap();
        h.meta.put_folder(&child).await.unwrap();

        assert!(
            h.meta
                .update_folder(&parent.id, None, Some(child.id))
                .await
                .is_err(),
            "moving a folder under its own descendant must be rejected"
        );
        assert!(
            h.meta
                .update_folder(&parent.id, None, Some(parent.id))
                .await
                .is_err(),
            "a folder must not be its own parent"
        );
    }

    #[tokio::test]
    async fn deleting_a_non_empty_folder_orphans_its_contents() {
        // Documents current behavior. See FINDING in
        // deleting_a_non_empty_folder_should_not_orphan_its_contents.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let folder = folder_owned_by(owner, "Reports", None);
        let subfolder = folder_owned_by(owner, "2026", Some(folder.id));
        let file = file_owned_by(owner, "q3.pdf", Some(folder.id));
        h.meta.put_folder(&folder).await.unwrap();
        h.meta.put_folder(&subfolder).await.unwrap();
        h.meta.put_file(&file).await.unwrap();

        h.meta.delete_folder(&folder.id).await.unwrap();

        assert!(matches!(
            h.meta.get_folder(&folder.id).await,
            Err(ServiceError::FolderNotFound(_))
        ));
        let orphaned_file = h.meta.get_file(&file.id).await.unwrap();
        assert_eq!(orphaned_file.folder_id, Some(folder.id));
        assert!(
            !orphaned_file.is_trashed,
            "the file was not trashed with it"
        );
        let orphaned_folder = h.meta.get_folder(&subfolder.id).await.unwrap();
        assert_eq!(orphaned_folder.parent_id, Some(folder.id));
        assert!(
            h.meta
                .list_folders(None, Some(owner))
                .await
                .unwrap()
                .is_empty(),
            "the subfolder is not promoted to the root either"
        );
    }

    #[tokio::test]
    #[ignore = "FINDING: delete_folder is an unconditional DeleteItem — it neither refuses a \
                non-empty folder nor cascades. Child files and subfolders keep pointing at the \
                dead id, so they vanish from every listing while still occupying storage"]
    async fn deleting_a_non_empty_folder_should_not_orphan_its_contents() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let folder = folder_owned_by(owner, "Reports", None);
        let file = file_owned_by(owner, "q3.pdf", Some(folder.id));
        h.meta.put_folder(&folder).await.unwrap();
        h.meta.put_file(&file).await.unwrap();

        let outcome = h.meta.delete_folder(&folder.id).await;
        let still_reachable = h
            .meta
            .get_file(&file.id)
            .await
            .map(|f| f.folder_id.is_none())
            .unwrap_or(false);
        assert!(
            outcome.is_err() || still_reachable,
            "deleting a non-empty folder must either be refused or reparent its contents"
        );
    }

    #[tokio::test]
    async fn deleting_a_folder_twice_is_accepted() {
        let h = harness().await;
        let folder = folder_owned_by(Uuid::new_v4(), "Once", None);
        h.meta.put_folder(&folder).await.unwrap();

        h.meta.delete_folder(&folder.id).await.unwrap();
        h.meta.delete_folder(&folder.id).await.unwrap();
        assert_eq!(h.dynamo.row_count(FOLDERS), 0);
    }

    // ── Versions ──────────────────────────────────────────────────────────

    #[tokio::test]
    async fn versions_are_scoped_to_their_file_and_returned_newest_first() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        let other = file_owned_by(owner, "b.txt", None);
        for (index, version) in [1u32, 2, 3].iter().enumerate() {
            h.meta
                .put_version(&FileVersion {
                    file_id: file.id,
                    version: *version,
                    s3_key: format!("files/{}/v{version}", owner),
                    size_bytes: 10 * (index as u64 + 1),
                    created_by: owner,
                    created_at: at(index as i64),
                })
                .await
                .unwrap();
        }
        h.meta
            .put_version(&FileVersion {
                file_id: other.id,
                version: 1,
                s3_key: "files/other/v1".into(),
                size_bytes: 1,
                created_by: owner,
                created_at: at(0),
            })
            .await
            .unwrap();

        let versions = h.meta.list_versions(&file.id).await.unwrap();
        assert_eq!(
            versions.iter().map(|v| v.version).collect::<Vec<_>>(),
            vec![3, 2, 1]
        );
        assert!(versions.iter().all(|v| v.file_id == file.id));
        assert_eq!(h.meta.list_versions(&other.id).await.unwrap().len(), 1);
        assert!(h
            .meta
            .list_versions(&Uuid::new_v4())
            .await
            .unwrap()
            .is_empty());
    }

    #[tokio::test]
    async fn re_putting_the_same_version_replaces_it_rather_than_duplicating() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let file_id = Uuid::new_v4();
        let mut version = FileVersion {
            file_id,
            version: 1,
            s3_key: "files/v1".into(),
            size_bytes: 10,
            created_by: owner,
            created_at: at(0),
        };
        h.meta.put_version(&version).await.unwrap();
        version.size_bytes = 20;
        h.meta.put_version(&version).await.unwrap();

        let versions = h.meta.list_versions(&file_id).await.unwrap();
        assert_eq!(versions.len(), 1);
        assert_eq!(versions[0].size_bytes, 20);
    }

    // ── Shares: the permission matrix ─────────────────────────────────────

    #[tokio::test]
    async fn every_share_tier_round_trips_through_dynamodb() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        for tier in [SharePermission::Viewer, SharePermission::Editor] {
            let file = file_owned_by(owner, "a.txt", None);
            let recipient = Uuid::new_v4();
            h.meta.put_file(&file).await.unwrap();
            let record = share(file.id, owner, recipient, tier.clone());
            h.meta.put_share(&record).await.unwrap();

            let found = h
                .meta
                .find_existing_share(&file.id, &recipient)
                .await
                .unwrap()
                .expect("share exists");
            assert_eq!(found.permission, tier);
            assert_eq!(found.shared_by, owner);
            assert_eq!(found.id, record.id);

            let listed = h.meta.list_shares(&file.id).await.unwrap();
            assert_eq!(listed.len(), 1);
            assert_eq!(listed[0].permission, tier);
        }
    }

    #[tokio::test]
    async fn share_tier_does_not_gate_any_mutation() {
        // The matrix the API advertises (viewer = read-only) is not enforced:
        // with a `viewer` share in place, every mutating metadata operation
        // still succeeds. See FINDING in
        // share_tier_should_gate_mutations_for_a_viewer.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let viewer = Uuid::new_v4();
        let folder = folder_owned_by(owner, "Docs", None);
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_folder(&folder).await.unwrap();
        h.meta.put_file(&file).await.unwrap();
        h.meta
            .put_share(&share(file.id, owner, viewer, SharePermission::Viewer))
            .await
            .unwrap();

        assert!(h.meta.rename_file(&file.id, "renamed.txt").await.is_ok());
        assert!(h.meta.move_file(&file.id, Some(folder.id)).await.is_ok());
        assert!(h.meta.trash_file(&file.id).await.is_ok());
        assert!(h.meta.restore_file(&file.id).await.is_ok());
        assert!(h
            .meta
            .put_share(&share(
                file.id,
                viewer,
                Uuid::new_v4(),
                SharePermission::Editor
            ))
            .await
            .is_ok());
        assert!(h.meta.delete_file(&file.id).await.is_ok());
    }

    #[tokio::test]
    #[ignore = "FINDING: FileShare::permission is written and read but never consulted. A \
                `viewer` share grants exactly the same rights as `editor` — rename, move, trash, \
                restore, re-share and permanent delete all succeed — because no code path in \
                file-service compares the tier against the requested action"]
    async fn share_tier_should_gate_mutations_for_a_viewer() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let viewer = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();
        h.meta
            .put_share(&share(file.id, owner, viewer, SharePermission::Viewer))
            .await
            .unwrap();

        assert!(
            h.meta.rename_file(&file.id, "renamed.txt").await.is_err(),
            "a viewer must not be able to rename"
        );
    }

    #[tokio::test]
    async fn changing_a_tier_reuses_the_share_row() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let recipient = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();

        let mut record = share(file.id, owner, recipient, SharePermission::Viewer);
        h.meta.put_share(&record).await.unwrap();
        record.permission = SharePermission::Editor;
        h.meta.put_share(&record).await.unwrap();

        assert_eq!(h.dynamo.row_count(SHARES), 1);
        let found = h
            .meta
            .find_existing_share(&file.id, &recipient)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(found.permission, SharePermission::Editor);
    }

    #[tokio::test]
    async fn removing_a_share_that_does_not_exist_finds_nothing() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();

        // Never shared at all.
        assert!(h
            .meta
            .find_existing_share(&file.id, &Uuid::new_v4())
            .await
            .unwrap()
            .is_none());

        // Shared with someone else.
        let recipient = Uuid::new_v4();
        h.meta
            .put_share(&share(file.id, owner, recipient, SharePermission::Viewer))
            .await
            .unwrap();
        assert!(h
            .meta
            .find_existing_share(&file.id, &Uuid::new_v4())
            .await
            .unwrap()
            .is_none());

        // Right user, wrong file.
        assert!(h
            .meta
            .find_existing_share(&Uuid::new_v4(), &recipient)
            .await
            .unwrap()
            .is_none());
    }

    #[tokio::test]
    async fn deleting_a_share_is_idempotent_and_leaves_other_shares_alone() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();
        let kept = share(file.id, owner, Uuid::new_v4(), SharePermission::Viewer);
        let removed = share(file.id, owner, Uuid::new_v4(), SharePermission::Editor);
        h.meta.put_share(&kept).await.unwrap();
        h.meta.put_share(&removed).await.unwrap();

        h.meta.delete_share(&removed.id).await.unwrap();
        h.meta.delete_share(&removed.id).await.unwrap();
        h.meta.delete_share(&Uuid::new_v4()).await.unwrap();

        let remaining = h.meta.list_shares(&file.id).await.unwrap();
        assert_eq!(remaining.len(), 1);
        assert_eq!(remaining[0].id, kept.id);
    }

    #[tokio::test]
    async fn sharing_a_file_with_its_own_owner_is_accepted() {
        // Documents current behavior. See FINDING in
        // sharing_with_yourself_should_be_refused.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();
        h.meta
            .put_share(&share(file.id, owner, owner, SharePermission::Viewer))
            .await
            .unwrap();

        let shared_with_me = h.meta.list_shares_for_user(&owner).await.unwrap();
        assert_eq!(shared_with_me.len(), 1);
        assert_eq!(
            shared_with_me[0].shared_with, shared_with_me[0].shared_by,
            "the owner now appears in their own 'shared with me' list"
        );
    }

    #[tokio::test]
    #[ignore = "FINDING: share-with-self is accepted, so a file the caller already owns is \
                echoed back in GET /files/shared; nothing rejects shared_with == shared_by"]
    async fn sharing_with_yourself_should_be_refused() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();

        assert!(h
            .meta
            .put_share(&share(file.id, owner, owner, SharePermission::Viewer))
            .await
            .is_err());
    }

    #[tokio::test]
    async fn duplicate_share_rows_for_the_same_pair_are_allowed() {
        // Documents current behavior: the shares table is keyed by share id, so
        // two rows can describe the same (file, user) pair with conflicting
        // tiers. See FINDING in duplicate_shares_should_be_impossible.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let recipient = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();

        h.meta
            .put_share(&share(file.id, owner, recipient, SharePermission::Viewer))
            .await
            .unwrap();
        h.meta
            .put_share(&share(file.id, owner, recipient, SharePermission::Editor))
            .await
            .unwrap();

        assert_eq!(h.dynamo.row_count(SHARES), 2);
        let listed = h.meta.list_shares(&file.id).await.unwrap();
        assert_eq!(listed.len(), 2);
        assert!(h
            .meta
            .find_existing_share(&file.id, &recipient)
            .await
            .unwrap()
            .is_some());
    }

    #[tokio::test]
    #[ignore = "FINDING: nothing enforces one share per (file_id, shared_with). Two rows with \
                different tiers can coexist; find_existing_share returns whichever the scan \
                reaches first, so the effective permission is not deterministic and removing \
                the share only deletes one of the rows"]
    async fn duplicate_shares_should_be_impossible() {
        let h = harness().await;
        let owner = Uuid::new_v4();
        let recipient = Uuid::new_v4();
        let file = file_owned_by(owner, "a.txt", None);
        h.meta.put_file(&file).await.unwrap();

        h.meta
            .put_share(&share(file.id, owner, recipient, SharePermission::Viewer))
            .await
            .unwrap();
        assert!(
            h.meta
                .put_share(&share(file.id, owner, recipient, SharePermission::Editor))
                .await
                .is_err(),
            "a second share row for the same pair must be rejected"
        );
    }

    #[tokio::test]
    async fn share_listings_are_scoped_to_the_user_they_belong_to() {
        let h = harness().await;
        let alice = Uuid::new_v4();
        let bob = Uuid::new_v4();
        let carol = Uuid::new_v4();
        let alices_file = file_owned_by(alice, "a.txt", None);
        let bobs_file = file_owned_by(bob, "b.txt", None);
        h.meta.put_file(&alices_file).await.unwrap();
        h.meta.put_file(&bobs_file).await.unwrap();
        h.meta
            .put_share(&share(alices_file.id, alice, bob, SharePermission::Viewer))
            .await
            .unwrap();
        h.meta
            .put_share(&share(bobs_file.id, bob, carol, SharePermission::Editor))
            .await
            .unwrap();

        let bobs_inbox = h.meta.list_shares_for_user(&bob).await.unwrap();
        assert_eq!(bobs_inbox.len(), 1);
        assert_eq!(bobs_inbox[0].file_id, alices_file.id);

        assert!(
            h.meta
                .list_shares_for_user(&Uuid::new_v4())
                .await
                .unwrap()
                .is_empty(),
            "a stranger sees no shares"
        );

        let alices_outbox = h.meta.list_shares_by_owner(&alice).await.unwrap();
        assert_eq!(alices_outbox.len(), 1);
        assert_eq!(alices_outbox[0].shared_with, bob);
        assert!(
            !alices_outbox.iter().any(|s| s.file_id == bobs_file.id),
            "Alice must not see the shares Bob created"
        );
    }

    #[tokio::test]
    async fn listing_shares_for_a_file_with_none_returns_empty() {
        let h = harness().await;
        let file = file_owned_by(Uuid::new_v4(), "a.txt", None);
        h.meta.put_file(&file).await.unwrap();
        assert!(h.meta.list_shares(&file.id).await.unwrap().is_empty());
        assert!(h
            .meta
            .list_shares(&Uuid::new_v4())
            .await
            .unwrap()
            .is_empty());
    }

    #[tokio::test]
    async fn a_share_row_with_an_unknown_tier_fails_the_whole_listing() {
        // A row written by any other producer with a tier this service does not
        // know takes down the entire listing rather than being skipped.
        let h = harness().await;
        let file_id = Uuid::new_v4();
        let mut row = serde_json::Map::new();
        row.insert(
            "id".into(),
            serde_json::json!({"S": Uuid::new_v4().to_string()}),
        );
        row.insert(
            "file_id".into(),
            serde_json::json!({"S": file_id.to_string()}),
        );
        row.insert(
            "shared_with".into(),
            serde_json::json!({"S": Uuid::new_v4().to_string()}),
        );
        row.insert("permission".into(), serde_json::json!({"S": "owner"}));
        row.insert(
            "shared_by".into(),
            serde_json::json!({"S": Uuid::new_v4().to_string()}),
        );
        row.insert(
            "created_at".into(),
            serde_json::json!({"S": at(0).to_rfc3339()}),
        );
        h.dynamo.raw_put(SHARES, row);

        match h.meta.list_shares(&file_id).await {
            Err(ServiceError::DynamoError(message)) => {
                assert!(message.contains("invalid permission"), "{message}");
            }
            other => panic!("expected DynamoError, got {other:?}"),
        }
    }

    // ── Parsing: boundaries and negatives ─────────────────────────────────

    fn file_item(
        overrides: &[(&str, AttributeValue)],
    ) -> std::collections::HashMap<String, AttributeValue> {
        let id = Uuid::new_v4();
        let owner = Uuid::new_v4();
        let mut item = std::collections::HashMap::new();
        item.insert("id".into(), AttributeValue::S(id.to_string()));
        item.insert("name".into(), AttributeValue::S("a.txt".into()));
        item.insert("mime_type".into(), AttributeValue::S("text/plain".into()));
        item.insert("size_bytes".into(), AttributeValue::N("1".into()));
        item.insert("s3_key".into(), AttributeValue::S("files/a".into()));
        item.insert("owner_id".into(), AttributeValue::S(owner.to_string()));
        item.insert("version".into(), AttributeValue::N("1".into()));
        item.insert("is_trashed".into(), AttributeValue::Bool(false));
        item.insert("created_at".into(), AttributeValue::S(at(0).to_rfc3339()));
        item.insert("updated_at".into(), AttributeValue::S(at(0).to_rfc3339()));
        for (key, value) in overrides {
            item.insert((*key).to_string(), value.clone());
        }
        item
    }

    #[test]
    fn size_and_version_parse_at_their_type_boundaries() {
        // u64 / u32 limits: limit-1, limit, limit+1.
        for value in ["0", "1", "18446744073709551614", "18446744073709551615"] {
            let item = file_item(&[("size_bytes", AttributeValue::N(value.into()))]);
            assert_eq!(
                parse_file_metadata(&item).unwrap().size_bytes,
                value.parse::<u64>().unwrap()
            );
        }
        let too_big = file_item(&[(
            "size_bytes",
            AttributeValue::N("18446744073709551616".into()),
        )]);
        assert!(matches!(
            parse_file_metadata(&too_big),
            Err(ServiceError::DynamoError(_))
        ));

        for value in ["0", "4294967294", "4294967295"] {
            let item = file_item(&[("version", AttributeValue::N(value.into()))]);
            assert_eq!(
                parse_file_metadata(&item).unwrap().version,
                value.parse::<u32>().unwrap()
            );
        }
        let overflow = file_item(&[("version", AttributeValue::N("4294967296".into()))]);
        assert!(matches!(
            parse_file_metadata(&overflow),
            Err(ServiceError::DynamoError(_))
        ));
    }

    #[test]
    fn negative_and_non_numeric_sizes_are_rejected() {
        for value in ["-1", "1.5", "1e3", "", " 1", "0x10", "NaN"] {
            let item = file_item(&[("size_bytes", AttributeValue::N(value.into()))]);
            match parse_file_metadata(&item) {
                Err(ServiceError::DynamoError(message)) => {
                    assert!(message.contains("size_bytes"), "{message}");
                }
                other => panic!("size_bytes={value:?} should be rejected, got {other:?}"),
            }
        }
    }

    #[test]
    fn attributes_of_the_wrong_dynamodb_type_are_rejected() {
        let string_size = file_item(&[("size_bytes", AttributeValue::S("1".into()))]);
        assert!(parse_file_metadata(&string_size).is_err());

        let string_flag = file_item(&[("is_trashed", AttributeValue::S("true".into()))]);
        match parse_file_metadata(&string_flag) {
            Err(ServiceError::DynamoError(message)) => {
                assert!(message.contains("is_trashed"), "{message}");
            }
            other => panic!("expected a bool-field error, got {other:?}"),
        }

        let numeric_name = file_item(&[("name", AttributeValue::N("1".into()))]);
        assert!(parse_file_metadata(&numeric_name).is_err());
    }

    #[test]
    fn every_required_file_attribute_is_actually_required() {
        for field in [
            "id",
            "name",
            "mime_type",
            "size_bytes",
            "s3_key",
            "owner_id",
            "version",
            "is_trashed",
            "created_at",
            "updated_at",
        ] {
            let mut item = file_item(&[]);
            item.remove(field);
            match parse_file_metadata(&item) {
                Err(ServiceError::DynamoError(message)) => {
                    assert!(message.contains(field), "{field}: {message}");
                }
                other => panic!("missing {field} should fail, got {other:?}"),
            }
        }
    }

    #[test]
    fn malformed_identifiers_and_timestamps_are_rejected() {
        let bad_uuid = file_item(&[("id", AttributeValue::S("not-a-uuid".into()))]);
        match parse_file_metadata(&bad_uuid) {
            Err(ServiceError::DynamoError(message)) => {
                assert!(message.contains("invalid UUID"), "{message}");
            }
            other => panic!("expected an invalid-UUID error, got {other:?}"),
        }

        let bad_folder = file_item(&[("folder_id", AttributeValue::S("".into()))]);
        assert!(parse_file_metadata(&bad_folder).is_err());

        for timestamp in ["", "2026-13-01T00:00:00Z", "1700000000", "2026-01-01"] {
            let item = file_item(&[("created_at", AttributeValue::S(timestamp.into()))]);
            match parse_file_metadata(&item) {
                Err(ServiceError::DynamoError(message)) => {
                    assert!(message.contains("invalid datetime"), "{message}");
                }
                other => panic!("created_at={timestamp:?} should fail, got {other:?}"),
            }
        }
    }

    #[test]
    fn timestamps_with_an_offset_are_normalized_to_utc() {
        let item = file_item(&[(
            "created_at",
            AttributeValue::S("2026-01-01T12:00:00+05:00".into()),
        )]);
        let parsed = parse_file_metadata(&item).unwrap();
        assert_eq!(parsed.created_at.to_rfc3339(), "2026-01-01T07:00:00+00:00");
    }

    #[test]
    fn unknown_attributes_are_ignored_rather_than_rejected() {
        let item = file_item(&[("tenant_id", AttributeValue::S("t-1".into()))]);
        assert!(parse_file_metadata(&item).is_ok());
    }

    #[test]
    fn folder_parsing_requires_its_fields_and_validates_the_parent() {
        let mut item = std::collections::HashMap::new();
        item.insert("id".into(), AttributeValue::S(Uuid::new_v4().to_string()));
        item.insert("name".into(), AttributeValue::S("Docs".into()));
        item.insert(
            "owner_id".into(),
            AttributeValue::S(Uuid::new_v4().to_string()),
        );
        item.insert("created_at".into(), AttributeValue::S(at(0).to_rfc3339()));
        item.insert("updated_at".into(), AttributeValue::S(at(0).to_rfc3339()));
        assert!(parse_folder(&item).unwrap().parent_id.is_none());

        let mut with_bad_parent = item.clone();
        with_bad_parent.insert("parent_id".into(), AttributeValue::S("nope".into()));
        assert!(parse_folder(&with_bad_parent).is_err());

        for field in ["id", "name", "owner_id", "created_at", "updated_at"] {
            let mut missing = item.clone();
            missing.remove(field);
            assert!(parse_folder(&missing).is_err(), "{field} must be required");
        }
    }

    #[test]
    fn version_parsing_requires_its_fields() {
        let mut item = std::collections::HashMap::new();
        item.insert(
            "file_id".into(),
            AttributeValue::S(Uuid::new_v4().to_string()),
        );
        item.insert("version".into(), AttributeValue::N("2".into()));
        item.insert("s3_key".into(), AttributeValue::S("files/v2".into()));
        item.insert("size_bytes".into(), AttributeValue::N("0".into()));
        item.insert(
            "created_by".into(),
            AttributeValue::S(Uuid::new_v4().to_string()),
        );
        item.insert("created_at".into(), AttributeValue::S(at(0).to_rfc3339()));
        assert_eq!(parse_file_version(&item).unwrap().size_bytes, 0);

        for field in [
            "file_id",
            "version",
            "s3_key",
            "size_bytes",
            "created_by",
            "created_at",
        ] {
            let mut missing = item.clone();
            missing.remove(field);
            assert!(
                parse_file_version(&missing).is_err(),
                "{field} must be required"
            );
        }
    }

    // ── Backend failures ──────────────────────────────────────────────────

    #[tokio::test]
    async fn a_backend_failure_is_reported_as_a_dynamo_error_not_a_not_found() {
        // A conditional-check failure means "no such row"; anything else must
        // stay a 500-class error rather than being flattened into a 404.
        let h = harness().await;
        let file = file_owned_by(Uuid::new_v4(), "a.txt", None);
        let folder = folder_owned_by(Uuid::new_v4(), "Docs", None);
        h.meta.put_file(&file).await.unwrap();
        h.meta.put_folder(&folder).await.unwrap();
        h.dynamo.set_failing(true);

        assert!(matches!(
            h.meta.get_file(&file.id).await,
            Err(ServiceError::DynamoError(_))
        ));
        assert!(matches!(
            h.meta.trash_file(&file.id).await,
            Err(ServiceError::DynamoError(_))
        ));
        assert!(matches!(
            h.meta.restore_file(&file.id).await,
            Err(ServiceError::DynamoError(_))
        ));
        assert!(matches!(
            h.meta.rename_file(&file.id, "b.txt").await,
            Err(ServiceError::DynamoError(_))
        ));
        assert!(matches!(
            h.meta.move_file(&file.id, None).await,
            Err(ServiceError::DynamoError(_))
        ));
        assert!(matches!(
            h.meta
                .update_folder(&folder.id, Some("x".into()), None)
                .await,
            Err(ServiceError::DynamoError(_))
        ));
        assert!(matches!(
            h.meta.get_folder(&folder.id).await,
            Err(ServiceError::DynamoError(_))
        ));
        assert!(matches!(
            h.meta.put_file(&file).await,
            Err(ServiceError::DynamoError(_))
        ));
        assert!(matches!(
            h.meta.list_files(None, None, false).await,
            Err(ServiceError::DynamoError(_))
        ));

        h.dynamo.set_failing(false);
        assert!(h.meta.get_file(&file.id).await.is_ok());
    }

    #[tokio::test]
    async fn one_malformed_row_fails_the_whole_file_listing() {
        // Documents current behavior: `list_files` propagates the first parse
        // error instead of skipping the bad row, so a single corrupt item hides
        // every other file the user owns.
        let h = harness().await;
        let owner = Uuid::new_v4();
        let good = file_owned_by(owner, "good.txt", None);
        h.meta.put_file(&good).await.unwrap();
        let mut corrupt = serde_json::Map::new();
        corrupt.insert(
            "id".into(),
            serde_json::json!({"S": Uuid::new_v4().to_string()}),
        );
        corrupt.insert("name".into(), serde_json::json!({"S": "corrupt.txt"}));
        h.dynamo.raw_put(FILES, corrupt);

        match h.meta.list_files(None, None, true).await {
            Err(ServiceError::DynamoError(message)) => {
                assert!(message.contains("mime_type"), "{message}");
            }
            other => panic!("expected DynamoError, got {other:?}"),
        }
    }

    #[test]
    fn share_parsing_accepts_every_tier_spelling_dynamodb_might_hold() {
        let file_id = Uuid::new_v4();
        let mut item = std::collections::HashMap::new();
        item.insert("id".into(), AttributeValue::S(Uuid::new_v4().to_string()));
        item.insert("file_id".into(), AttributeValue::S(file_id.to_string()));
        item.insert(
            "shared_with".into(),
            AttributeValue::S(Uuid::new_v4().to_string()),
        );
        item.insert(
            "shared_by".into(),
            AttributeValue::S(Uuid::new_v4().to_string()),
        );
        item.insert("created_at".into(), AttributeValue::S(at(0).to_rfc3339()));

        for (raw, expected) in [
            ("viewer", SharePermission::Viewer),
            ("Viewer", SharePermission::Viewer),
            ("VIEWER", SharePermission::Viewer),
            ("editor", SharePermission::Editor),
            ("Editor", SharePermission::Editor),
            ("EDITOR", SharePermission::Editor),
        ] {
            let mut candidate = item.clone();
            candidate.insert("permission".into(), AttributeValue::S(raw.into()));
            assert_eq!(parse_file_share(&candidate).unwrap().permission, expected);
        }

        for raw in ["", " ", "viewer ", "owner", "admin", "0"] {
            let mut candidate = item.clone();
            candidate.insert("permission".into(), AttributeValue::S(raw.into()));
            match parse_file_share(&candidate) {
                Err(ServiceError::DynamoError(message)) => {
                    assert!(message.contains("invalid permission"), "{message}");
                }
                other => panic!("permission={raw:?} should fail, got {other:?}"),
            }
        }

        let mut missing_permission = item.clone();
        assert!(parse_file_share(&missing_permission).is_err());
        missing_permission.insert("permission".into(), AttributeValue::S("viewer".into()));
        for field in ["id", "file_id", "shared_with", "shared_by", "created_at"] {
            let mut missing = missing_permission.clone();
            missing.remove(field);
            assert!(parse_file_share(&missing).is_err(), "{field} is required");
        }
    }
}

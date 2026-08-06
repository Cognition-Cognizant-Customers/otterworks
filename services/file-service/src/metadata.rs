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

/// WP-02 — folders, trash/restore and the share matrix.
///
/// `MetadataClient` talks to DynamoDB over HTTP, so these tests point a real
/// `aws_sdk_dynamodb::Client` at an in-process endpoint that speaks just enough
/// of the DynamoDB JSON protocol to reply with canned items. Nothing here
/// touches AWS, the clock, the filesystem or any other test: every case owns
/// its own listener on an ephemeral port and its own UUIDs are fixed
/// constants, so the suite is order-independent and safe under `--shuffle`.
#[cfg(test)]
mod folder_trash_share_tests {
    use super::*;
    use serde_json::{json, Value};
    use std::collections::{HashMap, VecDeque};
    use std::net::SocketAddr;
    use std::sync::{Arc, Mutex};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::TcpListener;

    // -- Fixed fixtures (no clock, no randomness) --

    const TS_EARLY: &str = "2026-01-02T03:04:05+00:00";
    const TS_LATE: &str = "2026-03-04T05:06:07+00:00";

    fn uuid_of(n: u128) -> Uuid {
        Uuid::from_u128(n)
    }

    fn owner() -> Uuid {
        uuid_of(0x0100)
    }
    fn other_user() -> Uuid {
        uuid_of(0x0200)
    }
    fn file_id() -> Uuid {
        uuid_of(0x0300)
    }
    fn folder_id() -> Uuid {
        uuid_of(0x0400)
    }
    fn child_folder_id() -> Uuid {
        uuid_of(0x0401)
    }
    fn grandchild_folder_id() -> Uuid {
        uuid_of(0x0402)
    }
    fn share_id() -> Uuid {
        uuid_of(0x0500)
    }

    // -- Fake DynamoDB endpoint --

    /// One canned HTTP reply.
    #[derive(Clone)]
    struct Reply {
        status: u16,
        body: String,
    }

    impl Reply {
        fn ok(body: impl Into<String>) -> Self {
            Reply {
                status: 200,
                body: body.into(),
            }
        }

        fn empty() -> Self {
            Reply::ok("{}")
        }

        fn error(status: u16, code: &str) -> Self {
            Reply {
                status,
                body: json!({
                    "__type": format!("com.amazonaws.dynamodb.v20120810#{code}"),
                    "message": format!("{code} raised by the fake endpoint"),
                })
                .to_string(),
            }
        }

        fn conditional_check_failed() -> Self {
            Reply::error(400, "ConditionalCheckFailedException")
        }

        fn internal_server_error() -> Self {
            Reply::error(500, "InternalServerError")
        }
    }

    #[derive(Clone)]
    struct Recorded {
        target: String,
        body: Value,
    }

    struct FakeState {
        replies: HashMap<String, VecDeque<Reply>>,
        requests: Vec<Recorded>,
    }

    /// An in-process stand-in for DynamoDB.
    ///
    /// Replies are keyed by the operation name from `X-Amz-Target`, so a test
    /// never depends on the order in which distinct operations are issued. When
    /// several replies are queued for one operation they are consumed in turn
    /// and the last one repeats, which is how multi-page scans and
    /// "call the same thing twice" cases are expressed.
    struct FakeDynamo {
        state: Arc<Mutex<FakeState>>,
        handle: tokio::task::JoinHandle<()>,
        addr: SocketAddr,
    }

    impl Drop for FakeDynamo {
        fn drop(&mut self) {
            self.handle.abort();
        }
    }

    impl FakeDynamo {
        async fn start(replies: Vec<(&str, Reply)>) -> Self {
            let mut queued: HashMap<String, VecDeque<Reply>> = HashMap::new();
            for (target, reply) in replies {
                queued
                    .entry(target.to_string())
                    .or_default()
                    .push_back(reply);
            }

            let state = Arc::new(Mutex::new(FakeState {
                replies: queued,
                requests: Vec::new(),
            }));

            let listener = TcpListener::bind("127.0.0.1:0")
                .await
                .expect("bind fake dynamodb endpoint");
            let addr = listener.local_addr().expect("fake endpoint address");

            let server_state = Arc::clone(&state);
            let handle = tokio::spawn(async move {
                while let Ok((stream, _)) = listener.accept().await {
                    let conn_state = Arc::clone(&server_state);
                    tokio::spawn(async move { serve_connection(stream, conn_state).await });
                }
            });

            Self {
                state,
                handle,
                addr,
            }
        }

        /// A `MetadataClient` wired to this endpoint. Retries are disabled so a
        /// canned error surfaces immediately and the request log stays exact.
        fn metadata(&self) -> MetadataClient {
            let conf = aws_sdk_dynamodb::Config::builder()
                .behavior_version(aws_sdk_dynamodb::config::BehaviorVersion::latest())
                .region(aws_sdk_dynamodb::config::Region::new("us-east-1"))
                .credentials_provider(aws_sdk_dynamodb::config::Credentials::new(
                    "wp02-access-key",
                    "wp02-secret-key",
                    None,
                    None,
                    "wp02-tests",
                ))
                .endpoint_url(format!("http://{}", self.addr))
                .retry_config(aws_sdk_dynamodb::config::retry::RetryConfig::disabled())
                .timeout_config(aws_sdk_dynamodb::config::timeout::TimeoutConfig::disabled())
                .build();

            MetadataClient {
                client: aws_sdk_dynamodb::Client::from_conf(conf),
                files_table: "files".into(),
                folders_table: "folders".into(),
                versions_table: "versions".into(),
                shares_table: "shares".into(),
            }
        }

        fn requests_for(&self, target: &str) -> Vec<Value> {
            self.state
                .lock()
                .expect("request log")
                .requests
                .iter()
                .filter(|r| r.target == target)
                .map(|r| r.body.clone())
                .collect()
        }

        fn only_request_for(&self, target: &str) -> Value {
            let mut all = self.requests_for(target);
            assert_eq!(all.len(), 1, "expected exactly one {target} call");
            all.remove(0)
        }

        fn call_count(&self, target: &str) -> usize {
            self.requests_for(target).len()
        }

        fn targets_called(&self) -> Vec<String> {
            let mut targets: Vec<String> = self
                .state
                .lock()
                .expect("request log")
                .requests
                .iter()
                .map(|r| r.target.clone())
                .collect();
            targets.sort();
            targets.dedup();
            targets
        }
    }

    fn find_subslice(haystack: &[u8], needle: &[u8]) -> Option<usize> {
        haystack
            .windows(needle.len())
            .position(|window| window == needle)
    }

    fn header_value(head: &str, name: &str) -> Option<String> {
        head.lines().skip(1).find_map(|line| {
            let (key, value) = line.split_once(':')?;
            if key.trim().eq_ignore_ascii_case(name) {
                Some(value.trim().to_string())
            } else {
                None
            }
        })
    }

    fn reason_phrase(status: u16) -> &'static str {
        match status {
            200 => "OK",
            400 => "Bad Request",
            _ => "Internal Server Error",
        }
    }

    async fn serve_connection(mut stream: tokio::net::TcpStream, state: Arc<Mutex<FakeState>>) {
        let mut buf: Vec<u8> = Vec::new();

        loop {
            let head_end = loop {
                if let Some(pos) = find_subslice(&buf, b"\r\n\r\n") {
                    break pos;
                }
                let mut chunk = [0u8; 4096];
                match stream.read(&mut chunk).await {
                    Ok(0) | Err(_) => return,
                    Ok(n) => buf.extend_from_slice(&chunk[..n]),
                }
            };

            let head = String::from_utf8_lossy(&buf[..head_end]).into_owned();
            let content_length = header_value(&head, "content-length")
                .and_then(|v| v.parse::<usize>().ok())
                .unwrap_or(0);
            let body_start = head_end + 4;

            while buf.len() < body_start + content_length {
                let mut chunk = [0u8; 4096];
                match stream.read(&mut chunk).await {
                    Ok(0) | Err(_) => return,
                    Ok(n) => buf.extend_from_slice(&chunk[..n]),
                }
            }

            let body = buf[body_start..body_start + content_length].to_vec();
            buf.drain(..body_start + content_length);

            let target = header_value(&head, "x-amz-target")
                .and_then(|t| t.rsplit('.').next().map(str::to_string))
                .unwrap_or_default();

            let reply = {
                let mut guard = state.lock().expect("fake endpoint state");
                guard.requests.push(Recorded {
                    target: target.clone(),
                    body: serde_json::from_slice(&body).unwrap_or(Value::Null),
                });
                match guard.replies.get_mut(&target) {
                    Some(queue) if queue.len() > 1 => queue.pop_front().expect("queued reply"),
                    Some(queue) => queue.front().cloned().expect("queued reply"),
                    None => Reply::empty(),
                }
            };

            let response = format!(
                "HTTP/1.1 {} {}\r\ncontent-type: application/x-amz-json-1.0\r\nx-amzn-requestid: wp02\r\ncontent-length: {}\r\n\r\n{}",
                reply.status,
                reason_phrase(reply.status),
                reply.body.len(),
                reply.body
            );

            if stream.write_all(response.as_bytes()).await.is_err() {
                return;
            }
        }
    }

    // -- Wire-format item builders --

    fn file_item(id: Uuid, owner_id: Uuid, name: &str, trashed: bool) -> Value {
        json!({
            "id": {"S": id.to_string()},
            "name": {"S": name},
            "mime_type": {"S": "text/plain"},
            "size_bytes": {"N": "1024"},
            "s3_key": {"S": format!("files/{owner_id}/{id}")},
            "owner_id": {"S": owner_id.to_string()},
            "version": {"N": "1"},
            "is_trashed": {"BOOL": trashed},
            "created_at": {"S": TS_EARLY},
            "updated_at": {"S": TS_EARLY},
        })
    }

    fn folder_item(id: Uuid, parent: Option<Uuid>, name: &str) -> Value {
        let mut item = json!({
            "id": {"S": id.to_string()},
            "name": {"S": name},
            "owner_id": {"S": owner().to_string()},
            "created_at": {"S": TS_EARLY},
            "updated_at": {"S": TS_EARLY},
        });
        if let Some(parent_id) = parent {
            item["parent_id"] = json!({"S": parent_id.to_string()});
        }
        item
    }

    fn share_item(id: Uuid, shared_with: Uuid, permission: &str) -> Value {
        json!({
            "id": {"S": id.to_string()},
            "file_id": {"S": file_id().to_string()},
            "shared_with": {"S": shared_with.to_string()},
            "permission": {"S": permission},
            "shared_by": {"S": owner().to_string()},
            "created_at": {"S": TS_EARLY},
        })
    }

    fn version_item(version: u32) -> Value {
        json!({
            "file_id": {"S": file_id().to_string()},
            "version": {"N": version.to_string()},
            "s3_key": {"S": format!("files/v{version}")},
            "size_bytes": {"N": "2048"},
            "created_by": {"S": owner().to_string()},
            "created_at": {"S": TS_EARLY},
        })
    }

    fn get_item(item: Value) -> Reply {
        Reply::ok(json!({ "Item": item }).to_string())
    }

    fn no_item() -> Reply {
        Reply::ok("{}")
    }

    fn page(items: Vec<Value>) -> Reply {
        Reply::ok(json!({ "Count": items.len(), "Items": items }).to_string())
    }

    fn page_with_more(items: Vec<Value>, last_key: Uuid) -> Reply {
        Reply::ok(
            json!({
                "Count": items.len(),
                "Items": items,
                "LastEvaluatedKey": {"id": {"S": last_key.to_string()}},
            })
            .to_string(),
        )
    }

    fn expr(request: &Value, key: &str) -> String {
        request[key]
            .as_str()
            .unwrap_or_else(|| panic!("request has no {key}: {request}"))
            .to_string()
    }

    fn sample_file() -> FileMetadata {
        FileMetadata {
            id: file_id(),
            name: "quarterly.pdf".into(),
            mime_type: "application/pdf".into(),
            size_bytes: 1024,
            s3_key: "files/quarterly.pdf".into(),
            folder_id: None,
            owner_id: owner(),
            version: 1,
            is_trashed: false,
            created_at: parse_datetime(TS_EARLY).expect("fixture timestamp"),
            updated_at: parse_datetime(TS_EARLY).expect("fixture timestamp"),
        }
    }

    fn sample_folder(id: Uuid, parent: Option<Uuid>) -> Folder {
        Folder {
            id,
            name: "Documents".into(),
            parent_id: parent,
            owner_id: owner(),
            created_at: parse_datetime(TS_EARLY).expect("fixture timestamp"),
            updated_at: parse_datetime(TS_EARLY).expect("fixture timestamp"),
        }
    }

    fn sample_share(permission: SharePermission, shared_with: Uuid) -> FileShare {
        FileShare {
            id: share_id(),
            file_id: file_id(),
            shared_with,
            permission,
            shared_by: owner(),
            created_at: parse_datetime(TS_EARLY).expect("fixture timestamp"),
        }
    }

    // ==================================================================
    // Folder CRUD
    // ==================================================================

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn put_folder_at_root_omits_parent_id() {
        let fake = FakeDynamo::start(vec![("PutItem", Reply::empty())]).await;

        fake.metadata()
            .put_folder(&sample_folder(folder_id(), None))
            .await
            .expect("put_folder");

        let item = &fake.only_request_for("PutItem")["Item"];
        assert_eq!(item["id"]["S"], folder_id().to_string());
        assert!(
            item.get("parent_id").is_none(),
            "a root folder must not persist a parent_id: {item}"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn put_folder_with_parent_persists_parent_id() {
        let fake = FakeDynamo::start(vec![("PutItem", Reply::empty())]).await;

        fake.metadata()
            .put_folder(&sample_folder(child_folder_id(), Some(folder_id())))
            .await
            .expect("put_folder");

        let item = &fake.only_request_for("PutItem")["Item"];
        assert_eq!(item["parent_id"]["S"], folder_id().to_string());
        assert_eq!(fake.only_request_for("PutItem")["TableName"], "folders");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn get_folder_parses_a_nested_folder() {
        let fake = FakeDynamo::start(vec![(
            "GetItem",
            get_item(folder_item(
                child_folder_id(),
                Some(folder_id()),
                "Invoices",
            )),
        )])
        .await;

        let folder = fake
            .metadata()
            .get_folder(&child_folder_id())
            .await
            .expect("get_folder");

        assert_eq!(folder.name, "Invoices");
        assert_eq!(folder.parent_id, Some(folder_id()));
        assert_eq!(fake.only_request_for("GetItem")["TableName"], "folders");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn get_folder_missing_item_is_folder_not_found() {
        let fake = FakeDynamo::start(vec![("GetItem", no_item())]).await;

        let err = fake
            .metadata()
            .get_folder(&folder_id())
            .await
            .expect_err("absent folder must not resolve");

        assert!(matches!(err, ServiceError::FolderNotFound(id) if id == folder_id().to_string()));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn get_folder_dynamo_failure_is_not_reported_as_missing() {
        let fake = FakeDynamo::start(vec![("GetItem", Reply::internal_server_error())]).await;

        let err = fake
            .metadata()
            .get_folder(&folder_id())
            .await
            .expect_err("backend failure must surface");

        assert!(
            matches!(err, ServiceError::DynamoError(_)),
            "a backend outage must not masquerade as a 404: {err:?}"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn update_folder_rename_only_touches_name_and_timestamp() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(folder_item(folder_id(), None, "Renamed")),
            ),
        ])
        .await;

        let folder = fake
            .metadata()
            .update_folder(&folder_id(), Some("Renamed".into()), None)
            .await
            .expect("update_folder");

        assert_eq!(folder.name, "Renamed");
        let update = fake.only_request_for("UpdateItem");
        let expression = expr(&update, "UpdateExpression");
        assert!(expression.contains("#n = :n"), "{expression}");
        assert!(
            !expression.contains("parent_id"),
            "a rename must not move the folder: {expression}"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn update_folder_move_only_touches_parent_and_timestamp() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(folder_item(
                    child_folder_id(),
                    Some(folder_id()),
                    "Invoices",
                )),
            ),
        ])
        .await;

        let folder = fake
            .metadata()
            .update_folder(&child_folder_id(), None, Some(folder_id()))
            .await
            .expect("update_folder");

        assert_eq!(folder.parent_id, Some(folder_id()));
        let expression = expr(&fake.only_request_for("UpdateItem"), "UpdateExpression");
        assert!(expression.contains("parent_id = :p"), "{expression}");
        assert!(
            !expression.contains("#n"),
            "a move must not rename the folder: {expression}"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn update_folder_with_no_fields_still_writes_updated_at() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(folder_item(folder_id(), None, "Documents")),
            ),
        ])
        .await;

        fake.metadata()
            .update_folder(&folder_id(), None, None)
            .await
            .expect("empty update is accepted");

        // Boundary: zero updated fields still issues a write rather than
        // short-circuiting, so `updated_at` moves on a no-op request.
        let expression = expr(&fake.only_request_for("UpdateItem"), "UpdateExpression");
        assert_eq!(expression, "SET updated_at = :u");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn update_folder_on_missing_folder_is_folder_not_found() {
        let fake = FakeDynamo::start(vec![("UpdateItem", Reply::conditional_check_failed())]).await;

        let err = fake
            .metadata()
            .update_folder(&folder_id(), Some("Renamed".into()), None)
            .await
            .expect_err("updating an absent folder must fail");

        assert!(matches!(err, ServiceError::FolderNotFound(_)), "{err:?}");
        assert_eq!(
            fake.call_count("GetItem"),
            0,
            "a failed update must not re-read the folder"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn update_folder_backend_failure_is_dynamo_error() {
        let fake = FakeDynamo::start(vec![("UpdateItem", Reply::internal_server_error())]).await;

        let err = fake
            .metadata()
            .update_folder(&folder_id(), Some("Renamed".into()), None)
            .await
            .expect_err("backend failure must surface");

        assert!(matches!(err, ServiceError::DynamoError(_)), "{err:?}");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn delete_folder_removes_the_folder_row() {
        let fake = FakeDynamo::start(vec![("DeleteItem", Reply::empty())]).await;

        fake.metadata()
            .delete_folder(&folder_id())
            .await
            .expect("delete_folder");

        let request = fake.only_request_for("DeleteItem");
        assert_eq!(request["TableName"], "folders");
        assert_eq!(request["Key"]["id"]["S"], folder_id().to_string());
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn delete_folder_backend_failure_is_dynamo_error() {
        let fake = FakeDynamo::start(vec![("DeleteItem", Reply::internal_server_error())]).await;

        let err = fake
            .metadata()
            .delete_folder(&folder_id())
            .await
            .expect_err("backend failure must surface");

        assert!(matches!(err, ServiceError::DynamoError(_)), "{err:?}");
    }

    /// WP-02 finding F1 (genuine defect, pinned not fixed).
    ///
    /// `delete_folder` is an unconditional `DeleteItem`: it never looks for
    /// child folders or files, so deleting a populated folder succeeds and
    /// orphans everything inside it (the children keep a `parent_id` /
    /// `folder_id` pointing at a row that no longer exists). This test pins
    /// today's behaviour; the desired behaviour is asserted by the ignored
    /// test below.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn delete_non_empty_folder_currently_succeeds_and_orphans_children() {
        let fake = FakeDynamo::start(vec![
            (
                "Scan",
                page(vec![folder_item(
                    child_folder_id(),
                    Some(folder_id()),
                    "Invoices",
                )]),
            ),
            ("DeleteItem", Reply::empty()),
        ])
        .await;
        let meta = fake.metadata();

        let children = meta
            .list_folders(Some(folder_id()), None)
            .await
            .expect("list children");
        assert_eq!(children.len(), 1, "fixture must have a child folder");

        meta.delete_folder(&folder_id())
            .await
            .expect("delete of a populated folder currently succeeds");

        assert_eq!(
            fake.call_count("DeleteItem"),
            1,
            "exactly one row is deleted - the children are left behind"
        );
        assert!(
            !fake
                .targets_called()
                .contains(&"BatchWriteItem".to_string()),
            "no cascade is attempted: {:?}",
            fake.targets_called()
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    #[ignore = "expected-fail, WP-02 finding F1: delete_folder does not check for children"]
    async fn delete_non_empty_folder_should_be_rejected() {
        let fake = FakeDynamo::start(vec![
            (
                "Scan",
                page(vec![folder_item(
                    child_folder_id(),
                    Some(folder_id()),
                    "Invoices",
                )]),
            ),
            ("DeleteItem", Reply::empty()),
        ])
        .await;

        let err = fake
            .metadata()
            .delete_folder(&folder_id())
            .await
            .expect_err("deleting a populated folder should be refused");

        assert!(matches!(err, ServiceError::BadRequest(_)), "{err:?}");
    }

    // ==================================================================
    // Folder listing
    // ==================================================================

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_folders_at_root_filters_on_absent_parent() {
        let fake = FakeDynamo::start(vec![(
            "Scan",
            page(vec![folder_item(folder_id(), None, "Documents")]),
        )])
        .await;

        let folders = fake
            .metadata()
            .list_folders(None, None)
            .await
            .expect("list_folders");

        assert_eq!(folders.len(), 1);
        let filter = expr(&fake.only_request_for("Scan"), "FilterExpression");
        assert_eq!(filter, "attribute_not_exists(parent_id)");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_folders_combines_parent_and_owner_filters() {
        let fake = FakeDynamo::start(vec![("Scan", page(vec![]))]).await;

        let folders = fake
            .metadata()
            .list_folders(Some(folder_id()), Some(owner()))
            .await
            .expect("list_folders");

        assert!(folders.is_empty());
        let request = fake.only_request_for("Scan");
        assert_eq!(
            expr(&request, "FilterExpression"),
            "parent_id = :parent_id AND owner_id = :owner_id"
        );
        assert_eq!(
            request["ExpressionAttributeValues"][":owner_id"]["S"],
            owner().to_string()
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_folders_follows_the_scan_paginator_to_the_last_page() {
        let fake = FakeDynamo::start(vec![
            (
                "Scan",
                page_with_more(
                    vec![folder_item(folder_id(), None, "Documents")],
                    folder_id(),
                ),
            ),
            (
                "Scan",
                page(vec![folder_item(child_folder_id(), None, "Archive")]),
            ),
        ])
        .await;

        let folders = fake
            .metadata()
            .list_folders(None, None)
            .await
            .expect("list_folders");

        assert_eq!(folders.len(), 2, "both pages must be drained");
        assert_eq!(fake.call_count("Scan"), 2);
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_folders_rejects_a_row_with_a_corrupt_uuid() {
        let mut corrupt = folder_item(folder_id(), None, "Documents");
        corrupt["id"] = json!({"S": "not-a-uuid"});
        let fake = FakeDynamo::start(vec![("Scan", page(vec![corrupt]))]).await;

        let err = fake
            .metadata()
            .list_folders(None, None)
            .await
            .expect_err("a corrupt row must not be silently skipped");

        assert!(matches!(err, ServiceError::DynamoError(_)), "{err:?}");
    }

    // ==================================================================
    // Folder cycles
    // ==================================================================

    /// WP-02 finding F2 (genuine defect, pinned not fixed).
    ///
    /// `update_folder` writes `parent_id` with no ancestry walk, so a folder
    /// can be moved underneath its own descendant. The resulting cycle
    /// detaches the whole subtree from every root listing, because
    /// `list_folders(None, ..)` only matches rows with no `parent_id`.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn moving_a_folder_into_its_own_descendant_currently_succeeds() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(folder_item(
                    folder_id(),
                    Some(grandchild_folder_id()),
                    "Documents",
                )),
            ),
        ])
        .await;

        // Documents -> Invoices -> 2026, then Documents is moved under 2026.
        let cycled = fake
            .metadata()
            .update_folder(&folder_id(), None, Some(grandchild_folder_id()))
            .await
            .expect("cycle-creating move currently succeeds");

        assert_eq!(cycled.parent_id, Some(grandchild_folder_id()));
        assert_eq!(
            fake.call_count("Scan"),
            0,
            "no ancestry walk is performed before the write"
        );
    }

    /// Degenerate case of the same defect: a folder can be made its own parent.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn moving_a_folder_into_itself_currently_succeeds() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(folder_item(folder_id(), Some(folder_id()), "Documents")),
            ),
        ])
        .await;

        let folder = fake
            .metadata()
            .update_folder(&folder_id(), None, Some(folder_id()))
            .await
            .expect("self-parenting currently succeeds");

        assert_eq!(folder.parent_id, Some(folder.id));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    #[ignore = "expected-fail, WP-02 finding F2: update_folder has no cycle detection"]
    async fn moving_a_folder_into_its_own_descendant_should_be_rejected() {
        let fake = FakeDynamo::start(vec![
            (
                "GetItem",
                get_item(folder_item(
                    grandchild_folder_id(),
                    Some(child_folder_id()),
                    "2026",
                )),
            ),
            ("UpdateItem", Reply::empty()),
        ])
        .await;

        let err = fake
            .metadata()
            .update_folder(&folder_id(), None, Some(grandchild_folder_id()))
            .await
            .expect_err("a move that creates a cycle should be refused");

        assert!(matches!(err, ServiceError::BadRequest(_)), "{err:?}");
    }

    // ==================================================================
    // Trash / restore
    // ==================================================================

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn trash_file_sets_the_trashed_flag() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "quarterly.pdf", true)),
            ),
        ])
        .await;

        let file = fake
            .metadata()
            .trash_file(&file_id())
            .await
            .expect("trash_file");

        assert!(file.is_trashed);
        let update = fake.only_request_for("UpdateItem");
        assert_eq!(update["ExpressionAttributeValues"][":t"]["BOOL"], true);
        assert_eq!(expr(&update, "ConditionExpression"), "attribute_exists(id)");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn trash_file_that_does_not_exist_is_file_not_found() {
        let fake = FakeDynamo::start(vec![("UpdateItem", Reply::conditional_check_failed())]).await;

        let err = fake
            .metadata()
            .trash_file(&file_id())
            .await
            .expect_err("trashing an absent file must fail");

        assert!(matches!(err, ServiceError::FileNotFound(id) if id == file_id().to_string()));
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn trash_file_backend_failure_is_not_reported_as_missing() {
        let fake = FakeDynamo::start(vec![("UpdateItem", Reply::internal_server_error())]).await;

        let err = fake
            .metadata()
            .trash_file(&file_id())
            .await
            .expect_err("backend failure must surface");

        assert!(
            matches!(err, ServiceError::DynamoError(_)),
            "only a conditional-check failure means 'missing': {err:?}"
        );
    }

    /// Idempotency: trashing an already-trashed file is a successful no-op
    /// rather than a conflict, and the second call is byte-identical.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn trashing_twice_is_idempotent() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "quarterly.pdf", true)),
            ),
        ])
        .await;
        let meta = fake.metadata();

        let first = meta.trash_file(&file_id()).await.expect("first trash");
        let second = meta.trash_file(&file_id()).await.expect("second trash");

        assert!(first.is_trashed && second.is_trashed);
        assert_eq!(first.id, second.id);
        assert_eq!(first.updated_at, second.updated_at);
        assert_eq!(fake.call_count("UpdateItem"), 2);
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn restore_file_clears_the_trashed_flag() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "quarterly.pdf", false)),
            ),
        ])
        .await;

        let file = fake
            .metadata()
            .restore_file(&file_id())
            .await
            .expect("restore_file");

        assert!(!file.is_trashed);
        assert_eq!(
            fake.only_request_for("UpdateItem")["ExpressionAttributeValues"][":t"]["BOOL"],
            false
        );
    }

    /// WP-02 finding F4 (design gap, pinned not fixed).
    ///
    /// `restore_file` does not require the file to be trashed, so restoring a
    /// live file is a silent success that still bumps `updated_at`. Nothing
    /// distinguishes "restored" from "no-op" for the caller.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn restoring_a_file_that_is_not_trashed_currently_succeeds() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "quarterly.pdf", false)),
            ),
        ])
        .await;

        let file = fake
            .metadata()
            .restore_file(&file_id())
            .await
            .expect("restoring a live file currently succeeds");

        assert!(!file.is_trashed);
        let condition = expr(&fake.only_request_for("UpdateItem"), "ConditionExpression");
        assert_eq!(
            condition, "attribute_exists(id)",
            "existence is the only guard - is_trashed is not checked"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn restore_file_that_does_not_exist_is_file_not_found() {
        let fake = FakeDynamo::start(vec![("UpdateItem", Reply::conditional_check_failed())]).await;

        let err = fake
            .metadata()
            .restore_file(&file_id())
            .await
            .expect_err("restoring an absent file must fail");

        assert!(matches!(err, ServiceError::FileNotFound(_)), "{err:?}");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn trash_then_restore_round_trips_the_flag() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "quarterly.pdf", true)),
            ),
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "quarterly.pdf", false)),
            ),
        ])
        .await;
        let meta = fake.metadata();

        assert!(meta.trash_file(&file_id()).await.expect("trash").is_trashed);
        assert!(
            !meta
                .restore_file(&file_id())
                .await
                .expect("restore")
                .is_trashed
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_trashed_filters_on_the_trashed_flag_only() {
        let fake = FakeDynamo::start(vec![(
            "Scan",
            page(vec![file_item(file_id(), owner(), "quarterly.pdf", true)]),
        )])
        .await;

        let files = fake
            .metadata()
            .list_trashed(None)
            .await
            .expect("list_trashed");

        assert_eq!(files.len(), 1);
        let request = fake.only_request_for("Scan");
        assert_eq!(expr(&request, "FilterExpression"), "is_trashed = :trashed");
        assert_eq!(
            request["ExpressionAttributeValues"][":trashed"]["BOOL"],
            true
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_trashed_scopes_to_the_owner_when_given_one() {
        let fake = FakeDynamo::start(vec![("Scan", page(vec![]))]).await;

        let files = fake
            .metadata()
            .list_trashed(Some(owner()))
            .await
            .expect("list_trashed");

        assert!(files.is_empty(), "an empty trash is not an error");
        assert_eq!(
            expr(&fake.only_request_for("Scan"), "FilterExpression"),
            "is_trashed = :trashed AND owner_id = :owner_id"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_trashed_returns_most_recently_touched_first() {
        let mut older = file_item(file_id(), owner(), "older.pdf", true);
        older["updated_at"] = json!({"S": TS_EARLY});
        let mut newer = file_item(uuid_of(0x0301), owner(), "newer.pdf", true);
        newer["updated_at"] = json!({"S": TS_LATE});

        // Deliberately served oldest-first so the ordering comes from the code.
        let fake = FakeDynamo::start(vec![("Scan", page(vec![older, newer]))]).await;

        let files = fake
            .metadata()
            .list_trashed(None)
            .await
            .expect("list_trashed");

        assert_eq!(
            files.iter().map(|f| f.name.as_str()).collect::<Vec<_>>(),
            vec!["newer.pdf", "older.pdf"]
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_trashed_propagates_a_backend_failure() {
        let fake = FakeDynamo::start(vec![("Scan", Reply::internal_server_error())]).await;

        let err = fake
            .metadata()
            .list_trashed(None)
            .await
            .expect_err("backend failure must surface");

        assert!(matches!(err, ServiceError::DynamoError(_)), "{err:?}");
    }

    // ==================================================================
    // File metadata operations reached through the trash / share flows
    // ==================================================================

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn put_file_without_a_folder_omits_folder_id() {
        let fake = FakeDynamo::start(vec![("PutItem", Reply::empty())]).await;

        fake.metadata()
            .put_file(&sample_file())
            .await
            .expect("put_file");

        let item = &fake.only_request_for("PutItem")["Item"];
        assert!(item.get("folder_id").is_none(), "{item}");
        assert_eq!(item["is_trashed"]["BOOL"], false);
        assert_eq!(item["size_bytes"]["N"], "1024");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn put_file_in_a_folder_persists_folder_id() {
        let fake = FakeDynamo::start(vec![("PutItem", Reply::empty())]).await;
        let mut file = sample_file();
        file.folder_id = Some(folder_id());

        fake.metadata().put_file(&file).await.expect("put_file");

        assert_eq!(
            fake.only_request_for("PutItem")["Item"]["folder_id"]["S"],
            folder_id().to_string()
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn get_file_missing_item_is_file_not_found() {
        let fake = FakeDynamo::start(vec![("GetItem", no_item())]).await;

        let err = fake
            .metadata()
            .get_file(&file_id())
            .await
            .expect_err("absent file must not resolve");

        assert!(matches!(err, ServiceError::FileNotFound(_)), "{err:?}");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn delete_file_targets_the_files_table() {
        let fake = FakeDynamo::start(vec![("DeleteItem", Reply::empty())]).await;

        fake.metadata()
            .delete_file(&file_id())
            .await
            .expect("delete_file");

        assert_eq!(fake.only_request_for("DeleteItem")["TableName"], "files");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn rename_file_escapes_the_reserved_name_attribute() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "renamed.pdf", false)),
            ),
        ])
        .await;

        let file = fake
            .metadata()
            .rename_file(&file_id(), "renamed.pdf")
            .await
            .expect("rename_file");

        assert_eq!(file.name, "renamed.pdf");
        let update = fake.only_request_for("UpdateItem");
        assert_eq!(update["ExpressionAttributeNames"]["#n"], "name");
        assert_eq!(
            update["ExpressionAttributeValues"][":n"]["S"],
            "renamed.pdf"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn rename_file_that_does_not_exist_is_file_not_found() {
        let fake = FakeDynamo::start(vec![("UpdateItem", Reply::conditional_check_failed())]).await;

        let err = fake
            .metadata()
            .rename_file(&file_id(), "renamed.pdf")
            .await
            .expect_err("renaming an absent file must fail");

        assert!(matches!(err, ServiceError::FileNotFound(_)), "{err:?}");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn move_file_into_a_folder_sets_folder_id() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "quarterly.pdf", false)),
            ),
        ])
        .await;

        fake.metadata()
            .move_file(&file_id(), Some(folder_id()))
            .await
            .expect("move_file");

        let update = fake.only_request_for("UpdateItem");
        assert_eq!(
            expr(&update, "UpdateExpression"),
            "SET folder_id = :f, updated_at = :u"
        );
        assert_eq!(
            update["ExpressionAttributeValues"][":f"]["S"],
            folder_id().to_string()
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn move_file_to_the_root_removes_folder_id() {
        let fake = FakeDynamo::start(vec![
            ("UpdateItem", Reply::empty()),
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "quarterly.pdf", false)),
            ),
        ])
        .await;

        let file = fake
            .metadata()
            .move_file(&file_id(), None)
            .await
            .expect("move_file");

        assert!(file.folder_id.is_none());
        assert_eq!(
            expr(&fake.only_request_for("UpdateItem"), "UpdateExpression"),
            "SET updated_at = :u REMOVE folder_id"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn move_file_that_does_not_exist_is_file_not_found() {
        let fake = FakeDynamo::start(vec![("UpdateItem", Reply::conditional_check_failed())]).await;

        let err = fake
            .metadata()
            .move_file(&file_id(), Some(folder_id()))
            .await
            .expect_err("moving an absent file must fail");

        assert!(matches!(err, ServiceError::FileNotFound(_)), "{err:?}");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_files_without_filters_sends_no_filter_expression() {
        let fake = FakeDynamo::start(vec![("Scan", page(vec![]))]).await;

        fake.metadata()
            .list_files(None, None, true)
            .await
            .expect("list_files");

        let request = fake.only_request_for("Scan");
        assert!(
            request.get("FilterExpression").is_none(),
            "include_trashed=true with no scoping must scan unfiltered: {request}"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_files_excludes_trashed_rows_by_default() {
        let fake = FakeDynamo::start(vec![(
            "Scan",
            page(vec![file_item(file_id(), owner(), "quarterly.pdf", false)]),
        )])
        .await;

        let files = fake
            .metadata()
            .list_files(Some(folder_id()), Some(owner()), false)
            .await
            .expect("list_files");

        assert_eq!(files.len(), 1);
        assert_eq!(
            expr(&fake.only_request_for("Scan"), "FilterExpression"),
            "folder_id = :folder_id AND owner_id = :owner_id AND is_trashed = :trashed"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_files_drains_every_scan_page() {
        let fake = FakeDynamo::start(vec![
            (
                "Scan",
                page_with_more(
                    vec![file_item(file_id(), owner(), "one.pdf", false)],
                    file_id(),
                ),
            ),
            (
                "Scan",
                page(vec![file_item(uuid_of(0x0302), owner(), "two.pdf", false)]),
            ),
        ])
        .await;

        let files = fake
            .metadata()
            .list_files(None, None, true)
            .await
            .expect("list_files");

        assert_eq!(files.len(), 2);
    }

    // ==================================================================
    // Versions
    // ==================================================================

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn put_version_writes_to_the_versions_table() {
        let fake = FakeDynamo::start(vec![("PutItem", Reply::empty())]).await;
        let version = FileVersion {
            file_id: file_id(),
            version: 2,
            s3_key: "files/v2".into(),
            size_bytes: 2048,
            created_by: owner(),
            created_at: parse_datetime(TS_EARLY).expect("fixture timestamp"),
        };

        fake.metadata()
            .put_version(&version)
            .await
            .expect("put_version");

        let request = fake.only_request_for("PutItem");
        assert_eq!(request["TableName"], "versions");
        assert_eq!(request["Item"]["version"]["N"], "2");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_versions_on_a_file_with_no_versions_is_empty() {
        let fake = FakeDynamo::start(vec![("Query", page(vec![]))]).await;

        let versions = fake
            .metadata()
            .list_versions(&file_id())
            .await
            .expect("list_versions");

        assert!(versions.is_empty(), "zero versions is not an error");
        assert_eq!(fake.only_request_for("Query")["ScanIndexForward"], false);
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_versions_returns_every_version_the_query_yields() {
        let fake = FakeDynamo::start(vec![(
            "Query",
            page(vec![version_item(3), version_item(2), version_item(1)]),
        )])
        .await;

        let versions = fake
            .metadata()
            .list_versions(&file_id())
            .await
            .expect("list_versions");

        assert_eq!(
            versions.iter().map(|v| v.version).collect::<Vec<_>>(),
            vec![3, 2, 1]
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_versions_rejects_a_row_with_a_corrupt_timestamp() {
        let mut corrupt = version_item(1);
        corrupt["created_at"] = json!({"S": "yesterday"});
        let fake = FakeDynamo::start(vec![("Query", page(vec![corrupt]))]).await;

        let err = fake
            .metadata()
            .list_versions(&file_id())
            .await
            .expect_err("a corrupt row must not be silently skipped");

        assert!(matches!(err, ServiceError::DynamoError(_)), "{err:?}");
    }

    // ==================================================================
    // Shares
    // ==================================================================

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn put_share_stores_the_permission_in_lowercase() {
        let fake = FakeDynamo::start(vec![("PutItem", Reply::empty())]).await;

        fake.metadata()
            .put_share(&sample_share(SharePermission::Editor, other_user()))
            .await
            .expect("put_share");

        let request = fake.only_request_for("PutItem");
        assert_eq!(request["TableName"], "shares");
        assert_eq!(request["Item"]["permission"]["S"], "editor");
    }

    /// WP-02 finding F5 (design gap, pinned not fixed): sharing a file with
    /// its own owner is accepted and creates a redundant row, which then shows
    /// up in that user's "shared with me" listing alongside their own files.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn sharing_a_file_with_yourself_currently_succeeds() {
        let fake = FakeDynamo::start(vec![("PutItem", Reply::empty())]).await;

        fake.metadata()
            .put_share(&sample_share(SharePermission::Viewer, owner()))
            .await
            .expect("self-share currently succeeds");

        let item = &fake.only_request_for("PutItem")["Item"];
        assert_eq!(item["shared_with"]["S"], item["shared_by"]["S"]);
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn find_existing_share_returns_none_when_nothing_matches() {
        let fake = FakeDynamo::start(vec![("Scan", page(vec![]))]).await;

        let found = fake
            .metadata()
            .find_existing_share(&file_id(), &other_user())
            .await
            .expect("find_existing_share");

        assert!(found.is_none());
        assert_eq!(
            expr(&fake.only_request_for("Scan"), "FilterExpression"),
            "file_id = :fid AND shared_with = :uid"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn find_existing_share_returns_the_matching_row() {
        let fake = FakeDynamo::start(vec![(
            "Scan",
            page(vec![share_item(share_id(), other_user(), "viewer")]),
        )])
        .await;

        let found = fake
            .metadata()
            .find_existing_share(&file_id(), &other_user())
            .await
            .expect("find_existing_share")
            .expect("share is present");

        assert_eq!(found.id, share_id());
        assert_eq!(found.permission, SharePermission::Viewer);
    }

    /// Removing a share that does not exist: the metadata layer's
    /// `DeleteItem` is unconditional, so it reports success for an id that was
    /// never stored. The 404 users see comes from the handler's prior
    /// `find_existing_share` lookup, not from this call.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn deleting_a_share_that_does_not_exist_reports_success() {
        let fake = FakeDynamo::start(vec![("DeleteItem", Reply::empty())]).await;

        fake.metadata()
            .delete_share(&uuid_of(0xDEAD))
            .await
            .expect("unconditional delete reports success");

        let request = fake.only_request_for("DeleteItem");
        assert_eq!(request["TableName"], "shares");
        assert!(
            request.get("ConditionExpression").is_none(),
            "no existence guard is sent: {request}"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn delete_share_backend_failure_is_dynamo_error() {
        let fake = FakeDynamo::start(vec![("DeleteItem", Reply::internal_server_error())]).await;

        let err = fake
            .metadata()
            .delete_share(&share_id())
            .await
            .expect_err("backend failure must surface");

        assert!(matches!(err, ServiceError::DynamoError(_)), "{err:?}");
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_shares_for_a_file_filters_on_file_id() {
        let fake = FakeDynamo::start(vec![(
            "Scan",
            page(vec![
                share_item(share_id(), other_user(), "viewer"),
                share_item(uuid_of(0x0501), uuid_of(0x0201), "editor"),
            ]),
        )])
        .await;

        let shares = fake
            .metadata()
            .list_shares(&file_id())
            .await
            .expect("list_shares");

        assert_eq!(shares.len(), 2);
        assert_eq!(
            expr(&fake.only_request_for("Scan"), "FilterExpression"),
            "file_id = :fid"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_shares_for_user_filters_on_the_recipient() {
        let fake = FakeDynamo::start(vec![(
            "Scan",
            page(vec![share_item(share_id(), other_user(), "editor")]),
        )])
        .await;

        let shares = fake
            .metadata()
            .list_shares_for_user(&other_user())
            .await
            .expect("list_shares_for_user");

        assert_eq!(shares.len(), 1);
        assert_eq!(shares[0].permission, SharePermission::Editor);
        assert_eq!(
            expr(&fake.only_request_for("Scan"), "FilterExpression"),
            "shared_with = :uid"
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn list_shares_by_owner_filters_on_the_granting_user() {
        let fake = FakeDynamo::start(vec![("Scan", page(vec![]))]).await;

        let shares = fake
            .metadata()
            .list_shares_by_owner(&owner())
            .await
            .expect("list_shares_by_owner");

        assert!(shares.is_empty());
        assert_eq!(
            expr(&fake.only_request_for("Scan"), "FilterExpression"),
            "shared_by = :uid"
        );
    }

    /// An unknown permission string that reached storage fails the whole
    /// listing, and does so as a 500-class `DynamoError` rather than a
    /// data-quality error - one bad row hides every other share on the file.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_share_row_with_an_unknown_permission_fails_the_listing() {
        let fake = FakeDynamo::start(vec![(
            "Scan",
            page(vec![
                share_item(share_id(), other_user(), "viewer"),
                share_item(uuid_of(0x0502), uuid_of(0x0202), "superuser"),
            ]),
        )])
        .await;

        let err = fake
            .metadata()
            .list_shares(&file_id())
            .await
            .expect_err("an unknown permission must not be coerced");

        match err {
            ServiceError::DynamoError(message) => {
                assert!(
                    message.contains("invalid permission: superuser"),
                    "{message}"
                )
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn a_share_row_with_a_blank_permission_is_rejected() {
        let fake = FakeDynamo::start(vec![(
            "Scan",
            page(vec![share_item(share_id(), other_user(), "")]),
        )])
        .await;

        let err = fake
            .metadata()
            .list_shares_for_user(&other_user())
            .await
            .expect_err("a blank permission must not default to viewer");

        assert!(matches!(err, ServiceError::DynamoError(_)), "{err:?}");
    }

    // ==================================================================
    // Share permission matrix
    // ==================================================================

    /// Every action a share is supposed to gate.
    #[derive(Clone, Copy, Debug)]
    enum GatedAction {
        ReadMetadata,
        ListVersions,
        Rename,
        Move,
        Trash,
        Restore,
        Delete,
        AddShare,
        RemoveShare,
    }

    impl GatedAction {
        const ALL: [GatedAction; 9] = [
            GatedAction::ReadMetadata,
            GatedAction::ListVersions,
            GatedAction::Rename,
            GatedAction::Move,
            GatedAction::Trash,
            GatedAction::Restore,
            GatedAction::Delete,
            GatedAction::AddShare,
            GatedAction::RemoveShare,
        ];

        async fn attempt(self, meta: &MetadataClient) -> Result<(), ServiceError> {
            match self {
                GatedAction::ReadMetadata => meta.get_file(&file_id()).await.map(|_| ()),
                GatedAction::ListVersions => meta.list_versions(&file_id()).await.map(|_| ()),
                GatedAction::Rename => meta
                    .rename_file(&file_id(), "renamed-by-grantee.pdf")
                    .await
                    .map(|_| ()),
                GatedAction::Move => meta
                    .move_file(&file_id(), Some(folder_id()))
                    .await
                    .map(|_| ()),
                GatedAction::Trash => meta.trash_file(&file_id()).await.map(|_| ()),
                GatedAction::Restore => meta.restore_file(&file_id()).await.map(|_| ()),
                GatedAction::Delete => meta.delete_file(&file_id()).await,
                GatedAction::AddShare => {
                    meta.put_share(&sample_share(SharePermission::Editor, uuid_of(0x0210)))
                        .await
                }
                GatedAction::RemoveShare => meta.delete_share(&share_id()).await,
            }
        }
    }

    /// Permissions a share of each tier is intended to confer. This is the
    /// policy the product describes; it is asserted by the ignored test below.
    fn intended_to_be_allowed(permission: &SharePermission, action: GatedAction) -> bool {
        match action {
            GatedAction::ReadMetadata | GatedAction::ListVersions => true,
            GatedAction::Rename | GatedAction::Move | GatedAction::Trash | GatedAction::Restore => {
                *permission == SharePermission::Editor
            }
            // Destroying the file and re-granting access stay with the owner.
            GatedAction::Delete | GatedAction::AddShare | GatedAction::RemoveShare => false,
        }
    }

    async fn matrix_backend() -> FakeDynamo {
        FakeDynamo::start(vec![
            (
                "GetItem",
                get_item(file_item(file_id(), owner(), "quarterly.pdf", false)),
            ),
            ("UpdateItem", Reply::empty()),
            ("PutItem", Reply::empty()),
            ("DeleteItem", Reply::empty()),
            ("Query", page(vec![version_item(1)])),
            (
                "Scan",
                page(vec![share_item(share_id(), other_user(), "viewer")]),
            ),
        ])
        .await
    }

    /// WP-02 finding F3 (genuine defect, pinned not fixed).
    ///
    /// Table-driven sweep of every share tier against every action the tier is
    /// meant to gate. Each cell succeeds today: `MetadataClient` takes no
    /// caller identity at all, so the stored `permission` is decoration and a
    /// `viewer` grantee can rename, move, trash, delete and re-share the file.
    /// The intended policy is asserted by the ignored test that follows.
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn share_permission_matrix_gates_nothing_today() {
        let fake = matrix_backend().await;
        let meta = fake.metadata();

        for permission in [SharePermission::Viewer, SharePermission::Editor] {
            for action in GatedAction::ALL {
                let outcome = action.attempt(&meta).await;
                assert!(
                    outcome.is_ok(),
                    "{permission}/{action:?} unexpectedly failed: {outcome:?}"
                );
                assert!(
                    intended_to_be_allowed(&permission, action) || outcome.is_ok(),
                    "{permission}/{action:?} should be denied but the layer has no check"
                );
            }
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    #[ignore = "expected-fail, WP-02 finding F3: share permissions are stored but never enforced"]
    async fn share_permission_matrix_enforces_the_intended_policy() {
        let fake = matrix_backend().await;
        let meta = fake.metadata();

        for permission in [SharePermission::Viewer, SharePermission::Editor] {
            for action in GatedAction::ALL {
                let outcome = action.attempt(&meta).await;
                if intended_to_be_allowed(&permission, action) {
                    assert!(outcome.is_ok(), "{permission}/{action:?}: {outcome:?}");
                } else {
                    assert!(
                        matches!(outcome, Err(ServiceError::Forbidden(_))),
                        "{permission}/{action:?} should be forbidden, got {outcome:?}"
                    );
                }
            }
        }
    }

    /// Parse matrix for every permission string the API and stored data can
    /// present, including the casings the parser folds and the near-misses it
    /// must reject.
    #[test]
    fn share_permission_parse_matrix() {
        let cases: [(&str, Option<SharePermission>); 14] = [
            ("viewer", Some(SharePermission::Viewer)),
            ("Viewer", Some(SharePermission::Viewer)),
            ("VIEWER", Some(SharePermission::Viewer)),
            ("vIeWeR", Some(SharePermission::Viewer)),
            ("editor", Some(SharePermission::Editor)),
            ("Editor", Some(SharePermission::Editor)),
            ("EDITOR", Some(SharePermission::Editor)),
            (" viewer", None),
            ("viewer ", None),
            ("viewer\n", None),
            ("owner", None),
            ("admin", None),
            ("", None),
            ("vïewer", None),
        ];

        for (input, expected) in cases {
            assert_eq!(
                SharePermission::from_str_value(input),
                expected,
                "from_str_value({input:?})"
            );
        }
    }

    #[test]
    fn share_permission_round_trips_through_its_stored_form() {
        for permission in [SharePermission::Viewer, SharePermission::Editor] {
            let stored = permission.to_string();
            assert_eq!(
                SharePermission::from_str_value(&stored),
                Some(permission.clone()),
                "{stored} must survive a storage round trip"
            );
        }
    }

    // ==================================================================
    // Parsing: boundaries and corrupt rows
    // ==================================================================

    #[test]
    fn file_version_number_boundary_trio() {
        // u32 is the widest version the model can hold: max-1 and max parse,
        // max+1 does not.
        for (raw, expected) in [
            ("4294967294", Some(u32::MAX - 1)),
            ("4294967295", Some(u32::MAX)),
            ("4294967296", None),
        ] {
            let mut item = file_item(file_id(), owner(), "quarterly.pdf", false);
            item["version"] = json!({"N": raw});
            let parsed = parse_file_metadata(&attribute_map(&item))
                .ok()
                .map(|f| f.version);
            assert_eq!(parsed, expected, "version {raw}");
        }
    }

    #[test]
    fn file_size_boundary_trio() {
        for (raw, expected) in [
            ("18446744073709551614", Some(u64::MAX - 1)),
            ("18446744073709551615", Some(u64::MAX)),
            ("18446744073709551616", None),
        ] {
            let mut item = file_item(file_id(), owner(), "quarterly.pdf", false);
            item["size_bytes"] = json!({"N": raw});
            let parsed = parse_file_metadata(&attribute_map(&item))
                .ok()
                .map(|f| f.size_bytes);
            assert_eq!(parsed, expected, "size_bytes {raw}");
        }
    }

    #[test]
    fn a_negative_file_size_is_rejected() {
        let mut item = file_item(file_id(), owner(), "quarterly.pdf", false);
        item["size_bytes"] = json!({"N": "-1"});

        assert!(parse_file_metadata(&attribute_map(&item)).is_err());
    }

    #[test]
    fn a_zero_byte_file_parses() {
        let mut item = file_item(file_id(), owner(), "empty.txt", false);
        item["size_bytes"] = json!({"N": "0"});

        let file = parse_file_metadata(&attribute_map(&item)).expect("zero bytes is valid");
        assert_eq!(file.size_bytes, 0);
    }

    #[test]
    fn a_numeric_field_stored_as_a_string_is_rejected() {
        let mut item = file_item(file_id(), owner(), "quarterly.pdf", false);
        item["size_bytes"] = json!({"S": "1024"});

        assert!(parse_file_metadata(&attribute_map(&item)).is_err());
    }

    #[test]
    fn the_trashed_flag_stored_as_a_string_is_rejected() {
        let mut item = file_item(file_id(), owner(), "quarterly.pdf", false);
        item["is_trashed"] = json!({"S": "false"});

        assert!(
            parse_file_metadata(&attribute_map(&item)).is_err(),
            "a stringly-typed flag must not be coerced to false"
        );
    }

    #[test]
    fn a_corrupt_folder_reference_on_a_file_is_rejected() {
        let mut item = file_item(file_id(), owner(), "quarterly.pdf", false);
        item["folder_id"] = json!({"S": "not-a-uuid"});

        assert!(parse_file_metadata(&attribute_map(&item)).is_err());
    }

    #[test]
    fn timestamps_are_normalised_to_utc() {
        let mut item = file_item(file_id(), owner(), "quarterly.pdf", false);
        item["created_at"] = json!({"S": "2026-01-02T05:04:05+02:00"});

        let file = parse_file_metadata(&attribute_map(&item)).expect("offset timestamps parse");
        assert_eq!(file.created_at.to_rfc3339(), TS_EARLY);
    }

    #[test]
    fn a_folder_row_missing_its_owner_is_rejected() {
        let mut item = folder_item(folder_id(), None, "Documents");
        item.as_object_mut().expect("object").remove("owner_id");

        assert!(parse_folder(&attribute_map(&item)).is_err());
    }

    #[test]
    fn a_share_row_missing_its_recipient_is_rejected() {
        let mut item = share_item(share_id(), other_user(), "viewer");
        item.as_object_mut().expect("object").remove("shared_with");

        assert!(parse_file_share(&attribute_map(&item)).is_err());
    }

    /// Convert the JSON wire shape used by the fixtures into the
    /// `AttributeValue` map the parsers take, so boundary cases can be checked
    /// without a round trip through the endpoint.
    fn attribute_map(item: &Value) -> std::collections::HashMap<String, AttributeValue> {
        item.as_object()
            .expect("item object")
            .iter()
            .map(|(key, value)| {
                let attribute = if let Some(s) = value.get("S").and_then(Value::as_str) {
                    AttributeValue::S(s.to_string())
                } else if let Some(n) = value.get("N").and_then(Value::as_str) {
                    AttributeValue::N(n.to_string())
                } else if let Some(b) = value.get("BOOL").and_then(Value::as_bool) {
                    AttributeValue::Bool(b)
                } else {
                    panic!("unsupported fixture attribute: {value}")
                };
                (key.clone(), attribute)
            })
            .collect()
    }
}

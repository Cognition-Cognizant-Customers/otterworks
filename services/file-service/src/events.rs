use chrono::Utc;
use serde::Serialize;
use uuid::Uuid;

use crate::config::SnsConfig;
use crate::errors::ServiceError;

/// Publisher for file-service domain events via SNS.
#[derive(Clone)]
pub struct EventPublisher {
    client: aws_sdk_sns::Client,
    topic_arn: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FileEvent {
    pub event_type: String,
    pub file_id: String,
    pub owner_id: String,
    pub folder_id: Option<String>,
    #[serde(rename = "sharedWithUserId")]
    pub shared_with: Option<String>,
    pub timestamp: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mime_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size_bytes: Option<u64>,
}

impl EventPublisher {
    pub async fn new(sns_config: &SnsConfig, aws_config: &crate::config::AwsConfig) -> Self {
        let mut aws_cfg_builder = aws_config::defaults(aws_config::BehaviorVersion::latest())
            .region(aws_config::Region::new(aws_config.region.clone()));

        if let Some(endpoint) = &aws_config.endpoint_url {
            aws_cfg_builder = aws_cfg_builder.endpoint_url(endpoint);
        }

        let aws_cfg = aws_cfg_builder.load().await;
        let client = aws_sdk_sns::Client::new(&aws_cfg);

        Self {
            client,
            topic_arn: sns_config.topic_arn.clone(),
        }
    }

    #[cfg(test)]
    pub fn from_parts(client: aws_sdk_sns::Client, topic_arn: Option<String>) -> Self {
        Self { client, topic_arn }
    }

    async fn publish(&self, event: &FileEvent) -> Result<(), ServiceError> {
        let topic_arn = match &self.topic_arn {
            Some(arn) => arn,
            None => {
                tracing::debug!("SNS topic not configured, skipping event publish");
                return Ok(());
            }
        };

        let message =
            serde_json::to_string(event).map_err(|e| ServiceError::Internal(e.to_string()))?;

        let mut req = self.client.publish().topic_arn(topic_arn).message(&message);

        // message_group_id and message_deduplication_id are only valid for FIFO topics
        if topic_arn.ends_with(".fifo") {
            let dedup_id = format!("{}_{}", event.file_id, event.timestamp);
            req = req
                .message_group_id(&event.event_type)
                .message_deduplication_id(&dedup_id);
        }

        req.send()
            .await
            .map_err(|e| ServiceError::SnsError(e.to_string()))?;

        tracing::info!(
            event_type = %event.event_type,
            file_id = %event.file_id,
            "Published event to SNS"
        );
        Ok(())
    }

    pub async fn file_uploaded(
        &self,
        file_id: &Uuid,
        owner_id: &Uuid,
        folder_id: Option<&Uuid>,
        name: &str,
        mime_type: &str,
        size_bytes: u64,
    ) -> Result<(), ServiceError> {
        let event = FileEvent {
            event_type: "file_uploaded".into(),
            file_id: file_id.to_string(),
            owner_id: owner_id.to_string(),
            folder_id: folder_id.map(|f| f.to_string()),
            shared_with: None,
            timestamp: Utc::now().to_rfc3339(),
            name: Some(name.to_string()),
            mime_type: Some(mime_type.to_string()),
            size_bytes: Some(size_bytes),
        };
        self.publish(&event).await
    }

    pub async fn file_deleted(&self, file_id: &Uuid, owner_id: &Uuid) -> Result<(), ServiceError> {
        let event = FileEvent {
            event_type: "file_deleted".into(),
            file_id: file_id.to_string(),
            owner_id: owner_id.to_string(),
            folder_id: None,
            shared_with: None,
            timestamp: Utc::now().to_rfc3339(),
            name: None,
            mime_type: None,
            size_bytes: None,
        };
        self.publish(&event).await
    }

    pub async fn file_shared(
        &self,
        file_id: &Uuid,
        owner_id: &Uuid,
        shared_with: &Uuid,
    ) -> Result<(), ServiceError> {
        let event = FileEvent {
            event_type: "file_shared".into(),
            file_id: file_id.to_string(),
            owner_id: owner_id.to_string(),
            folder_id: None,
            shared_with: Some(shared_with.to_string()),
            timestamp: Utc::now().to_rfc3339(),
            name: None,
            mime_type: None,
            size_bytes: None,
        };
        self.publish(&event).await
    }

    pub async fn file_trashed(&self, file_id: &Uuid, owner_id: &Uuid) -> Result<(), ServiceError> {
        let event = FileEvent {
            event_type: "file_trashed".into(),
            file_id: file_id.to_string(),
            owner_id: owner_id.to_string(),
            folder_id: None,
            shared_with: None,
            timestamp: Utc::now().to_rfc3339(),
            name: None,
            mime_type: None,
            size_bytes: None,
        };
        self.publish(&event).await
    }

    pub async fn file_restored(
        &self,
        file_id: &Uuid,
        owner_id: &Uuid,
        folder_id: Option<&Uuid>,
        name: &str,
        mime_type: &str,
        size_bytes: u64,
    ) -> Result<(), ServiceError> {
        let event = FileEvent {
            event_type: "file_restored".into(),
            file_id: file_id.to_string(),
            owner_id: owner_id.to_string(),
            folder_id: folder_id.map(|f| f.to_string()),
            shared_with: None,
            timestamp: Utc::now().to_rfc3339(),
            name: Some(name.to_string()),
            mime_type: Some(mime_type.to_string()),
            size_bytes: Some(size_bytes),
        };
        self.publish(&event).await
    }

    pub async fn file_updated(
        &self,
        file_id: &Uuid,
        owner_id: &Uuid,
        folder_id: Option<&Uuid>,
        name: &str,
        mime_type: &str,
        size_bytes: u64,
    ) -> Result<(), ServiceError> {
        let event = FileEvent {
            event_type: "file_updated".into(),
            file_id: file_id.to_string(),
            owner_id: owner_id.to_string(),
            folder_id: folder_id.map(|f| f.to_string()),
            shared_with: None,
            timestamp: Utc::now().to_rfc3339(),
            name: Some(name.to_string()),
            mime_type: Some(mime_type.to_string()),
            size_bytes: Some(size_bytes),
        };
        self.publish(&event).await
    }

    pub async fn file_moved(
        &self,
        file_id: &Uuid,
        owner_id: &Uuid,
        folder_id: Option<&Uuid>,
    ) -> Result<(), ServiceError> {
        let event = FileEvent {
            event_type: "file_moved".into(),
            file_id: file_id.to_string(),
            owner_id: owner_id.to_string(),
            folder_id: folder_id.map(|f| f.to_string()),
            shared_with: None,
            timestamp: Utc::now().to_rfc3339(),
            name: None,
            mime_type: None,
            size_bytes: None,
        };
        self.publish(&event).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::{expected_request, http_response, sns_client};
    use aws_smithy_http_client::test_util::ReplayEvent;

    #[test]
    fn test_file_event_serialization() {
        let event = FileEvent {
            event_type: "file_uploaded".into(),
            file_id: Uuid::new_v4().to_string(),
            owner_id: Uuid::new_v4().to_string(),
            folder_id: None,
            shared_with: None,
            timestamp: Utc::now().to_rfc3339(),
            name: Some("test.txt".to_string()),
            mime_type: Some("text/plain".to_string()),
            size_bytes: Some(100),
        };
        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains("file_uploaded"));
        assert!(json.contains("eventType"));
        assert!(json.contains("fileId"));
        assert!(json.contains("ownerId"));
    }

    #[tokio::test]
    async fn publishing_without_a_topic_is_a_no_op() {
        let (client, http) = sns_client(vec![]);
        let publisher = EventPublisher::from_parts(client, None);

        publisher
            .file_deleted(&Uuid::new_v4(), &Uuid::new_v4())
            .await
            .expect("publish should be skipped");

        assert_eq!(http.actual_requests().count(), 0);
    }

    #[tokio::test]
    async fn a_configured_topic_receives_the_serialised_event() {
        let (client, http) = sns_client(vec![sns_publish_ok()]);
        let publisher = EventPublisher::from_parts(
            client,
            Some("arn:aws:sns:us-east-1:1:file-events".to_string()),
        );

        let file_id = Uuid::new_v4();
        let owner_id = Uuid::new_v4();
        publisher
            .file_uploaded(
                &file_id,
                &owner_id,
                None,
                "report.pdf",
                "application/pdf",
                12,
            )
            .await
            .expect("publish should succeed");

        let request = http.actual_requests().next().unwrap();
        let body = form_body(request.body().bytes().unwrap());
        assert!(body.contains("arn:aws:sns:us-east-1:1:file-events"));
        assert!(body.contains("file_uploaded"));
        assert!(body.contains(&file_id.to_string()));
        // Standard topics must not carry FIFO-only parameters.
        assert!(!body.contains("MessageGroupId"));
    }

    #[tokio::test]
    async fn a_fifo_topic_gets_a_group_and_deduplication_id() {
        let (client, http) = sns_client(vec![sns_publish_ok()]);
        let publisher = EventPublisher::from_parts(
            client,
            Some("arn:aws:sns:us-east-1:1:file-events.fifo".to_string()),
        );

        publisher
            .file_trashed(&Uuid::new_v4(), &Uuid::new_v4())
            .await
            .expect("publish should succeed");

        let request = http.actual_requests().next().unwrap();
        let body = form_body(request.body().bytes().unwrap());
        assert!(body.contains("MessageGroupId"));
        assert!(body.contains("MessageDeduplicationId"));
    }

    #[tokio::test]
    async fn an_sns_failure_maps_to_a_service_error() {
        let (client, _http) = sns_client(vec![ReplayEvent::new(
            expected_request("http://sns.local/"),
            http_response(
                403,
                r#"<ErrorResponse><Error><Code>AuthorizationError</Code><Message>denied</Message></Error></ErrorResponse>"#,
            ),
        )]);
        let publisher = EventPublisher::from_parts(
            client,
            Some("arn:aws:sns:us-east-1:1:file-events".to_string()),
        );

        let err = publisher
            .file_deleted(&Uuid::new_v4(), &Uuid::new_v4())
            .await
            .expect_err("publish should fail");

        assert!(
            matches!(err, ServiceError::SnsError(_)),
            "unexpected error: {err:?}"
        );
    }

    fn sns_publish_ok() -> ReplayEvent {
        ReplayEvent::new(
            expected_request("http://sns.local/"),
            http_response(
                200,
                r#"<PublishResponse><PublishResult><MessageId>m-1</MessageId></PublishResult></PublishResponse>"#,
            ),
        )
    }

    /// The SNS query protocol form-encodes its parameters; decode just enough
    /// of it to assert on ARNs and message contents.
    fn form_body(body: &[u8]) -> String {
        String::from_utf8(body.to_vec())
            .unwrap()
            .replace("%3A", ":")
    }

    #[test]
    fn test_file_event_with_folder() {
        let folder = Uuid::new_v4();
        let event = FileEvent {
            event_type: "file_moved".into(),
            file_id: Uuid::new_v4().to_string(),
            owner_id: Uuid::new_v4().to_string(),
            folder_id: Some(folder.to_string()),
            shared_with: None,
            timestamp: Utc::now().to_rfc3339(),
            name: None,
            mime_type: None,
            size_bytes: None,
        };
        let json = serde_json::to_string(&event).unwrap();
        assert!(json.contains(&folder.to_string()));
        assert!(json.contains("folderId"));
    }
}

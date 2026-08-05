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

    #[test]
    fn test_file_event_omits_optional_fields_when_absent() {
        let event = FileEvent {
            event_type: "file_deleted".into(),
            file_id: Uuid::new_v4().to_string(),
            owner_id: Uuid::new_v4().to_string(),
            folder_id: None,
            shared_with: None,
            timestamp: Utc::now().to_rfc3339(),
            name: None,
            mime_type: None,
            size_bytes: None,
        };
        let json = serde_json::to_string(&event).unwrap();
        assert!(!json.contains("\"name\""));
        assert!(!json.contains("mimeType"));
        assert!(!json.contains("sizeBytes"));
        // folder_id and shared_with are always present (as null) for consumers
        assert!(json.contains("\"folderId\":null"));
        assert!(json.contains("\"sharedWithUserId\":null"));
    }
}

#[cfg(test)]
mod publish_tests {
    use super::*;
    use aws_smithy_http_client::test_util::{ReplayEvent, StaticReplayClient};
    use aws_smithy_types::body::SdkBody;

    const PUBLISH_OK: &str = r#"<PublishResponse xmlns="http://sns.amazonaws.com/doc/2010-03-31/"><PublishResult><MessageId>00000000-0000-0000-0000-000000000000</MessageId></PublishResult><ResponseMetadata><RequestId>req-1</RequestId></ResponseMetadata></PublishResponse>"#;

    fn mock_publisher(
        topic_arn: Option<String>,
        events: Vec<ReplayEvent>,
    ) -> (EventPublisher, StaticReplayClient) {
        let http_client = StaticReplayClient::new(events);
        let conf = aws_sdk_sns::Config::builder()
            .behavior_version(aws_sdk_sns::config::BehaviorVersion::latest())
            .region(aws_sdk_sns::config::Region::new("us-east-1"))
            .credentials_provider(aws_credential_types::Credentials::for_tests())
            .http_client(http_client.clone())
            .build();
        let publisher = EventPublisher {
            client: aws_sdk_sns::Client::from_conf(conf),
            topic_arn,
        };
        (publisher, http_client)
    }

    fn sns_event() -> ReplayEvent {
        ReplayEvent::new(
            http::Request::builder()
                .uri("https://sns.us-east-1.amazonaws.com/")
                .body(SdkBody::empty())
                .unwrap(),
            http::Response::builder()
                .status(200)
                .body(SdkBody::from(PUBLISH_OK))
                .unwrap(),
        )
    }

    fn sent_body(http_client: &StaticReplayClient) -> String {
        let req = http_client.actual_requests().next().expect("request");
        String::from_utf8(req.body().bytes().expect("body").to_vec()).unwrap()
    }

    #[tokio::test]
    async fn test_publish_skipped_when_topic_not_configured() {
        // No replay events: any HTTP call would fail the test
        let (publisher, http_client) = mock_publisher(None, vec![]);

        publisher
            .file_deleted(&Uuid::new_v4(), &Uuid::new_v4())
            .await
            .unwrap();

        assert_eq!(http_client.actual_requests().count(), 0);
    }

    #[tokio::test]
    async fn test_file_shared_publishes_expected_message() {
        let topic = "arn:aws:sns:us-east-1:000000000000:file-events";
        let (publisher, http_client) = mock_publisher(Some(topic.into()), vec![sns_event()]);

        let file_id = Uuid::new_v4();
        let owner = Uuid::new_v4();
        let shared_with = Uuid::new_v4();
        publisher
            .file_shared(&file_id, &owner, &shared_with)
            .await
            .unwrap();

        let body = sent_body(&http_client);
        assert!(body.contains("Action=Publish"));
        assert!(body.contains("file_shared"));
        assert!(body.contains(&file_id.to_string()));
        assert!(body.contains(&shared_with.to_string()));
        // Standard (non-FIFO) topics must not carry FIFO-only parameters
        assert!(!body.contains("MessageGroupId"));
        assert!(!body.contains("MessageDeduplicationId"));
    }

    #[tokio::test]
    async fn test_fifo_topic_sets_group_and_dedup_ids() {
        let topic = "arn:aws:sns:us-east-1:000000000000:file-events.fifo";
        let (publisher, http_client) = mock_publisher(Some(topic.into()), vec![sns_event()]);

        let file_id = Uuid::new_v4();
        publisher
            .file_trashed(&file_id, &Uuid::new_v4())
            .await
            .unwrap();

        let body = sent_body(&http_client);
        assert!(body.contains("MessageGroupId=file_trashed"));
        assert!(body.contains("MessageDeduplicationId="));
        assert!(body.contains(&file_id.to_string()));
    }

    #[tokio::test]
    async fn test_publish_failure_surfaces_sns_error() {
        let topic = "arn:aws:sns:us-east-1:000000000000:file-events";
        let error_event = ReplayEvent::new(
            http::Request::builder()
                .uri("https://sns.us-east-1.amazonaws.com/")
                .body(SdkBody::empty())
                .unwrap(),
            http::Response::builder()
                .status(404)
                .body(SdkBody::from(
                    r#"<ErrorResponse><Error><Type>Sender</Type><Code>NotFound</Code><Message>Topic does not exist</Message></Error></ErrorResponse>"#,
                ))
                .unwrap(),
        );
        let (publisher, _http_client) = mock_publisher(Some(topic.into()), vec![error_event]);

        let err = publisher
            .file_deleted(&Uuid::new_v4(), &Uuid::new_v4())
            .await
            .unwrap_err();
        assert!(matches!(err, ServiceError::SnsError(_)));
    }
}

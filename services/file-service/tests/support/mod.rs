use aws_sdk_dynamodb::types::{
    AttributeDefinition, KeySchemaElement, KeyType, ScalarAttributeType, TableStatus,
};
use bytes::Bytes;
use file_service::{
    config::{AppConfig, AwsConfig, ServerConfig, SnsConfig},
    events::EventPublisher,
    metadata::MetadataClient,
    storage::S3Client,
};
use std::sync::{Once, OnceLock};
use std::time::Duration;
use tokio::sync::Mutex;

pub const FILES_TABLE: &str = "otterworks-file-metadata";
pub const FOLDERS_TABLE: &str = "otterworks-folders";
pub const VERSIONS_TABLE: &str = "otterworks-file-versions";
pub const SHARES_TABLE: &str = "otterworks-file-shares";

static CREDENTIALS: Once = Once::new();
static RESOURCE_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

#[allow(dead_code)]
pub struct TestContext {
    pub config: AppConfig,
    pub s3: S3Client,
    pub metadata: MetadataClient,
    pub events: EventPublisher,
    pub redis: redis::aio::ConnectionManager,
}

pub async fn setup() -> TestContext {
    let config = app_config();
    ensure_credentials();
    ensure_resources(&config.aws).await;

    let s3 = S3Client::new(&config.aws).await;
    let metadata = MetadataClient::new(&config.aws).await;
    let events = EventPublisher::new(&config.sns, &config.aws).await;

    let redis_host = std::env::var("REDIS_HOST").unwrap_or_else(|_| "localhost".into());
    let redis_port = std::env::var("REDIS_PORT").unwrap_or_else(|_| "6379".into());
    let redis_client =
        redis::Client::open(format!("redis://{redis_host}:{redis_port}")).expect("valid Redis URL");
    let redis = redis::aio::ConnectionManager::new(redis_client)
        .await
        .expect("Redis must be available for handler integration tests");

    TestContext {
        config,
        s3,
        metadata,
        events,
        redis,
    }
}

pub fn app_config() -> AppConfig {
    let endpoint_url =
        std::env::var("AWS_ENDPOINT_URL").unwrap_or_else(|_| "http://localhost:4566".into());
    let s3_bucket = std::env::var("S3_BUCKET").unwrap_or_else(|_| "otterworks-files".into());

    AppConfig {
        server: ServerConfig {
            port: 8082,
            max_upload_bytes: 104_857_600,
        },
        aws: AwsConfig {
            region: "us-east-1".into(),
            endpoint_url: Some(endpoint_url),
            s3_bucket,
            dynamodb_table: std::env::var("DYNAMODB_TABLE").unwrap_or_else(|_| FILES_TABLE.into()),
            dynamodb_folders_table: std::env::var("DYNAMODB_FOLDERS_TABLE")
                .unwrap_or_else(|_| FOLDERS_TABLE.into()),
            dynamodb_versions_table: std::env::var("DYNAMODB_VERSIONS_TABLE")
                .unwrap_or_else(|_| VERSIONS_TABLE.into()),
            dynamodb_shares_table: std::env::var("DYNAMODB_SHARES_TABLE")
                .unwrap_or_else(|_| SHARES_TABLE.into()),
        },
        sns: SnsConfig { topic_arn: None },
    }
}

#[allow(dead_code)]
pub fn unique_key(prefix: &str) -> String {
    format!("{prefix}/{}", uuid::Uuid::new_v4())
}

pub async fn ensure_resources(config: &AwsConfig) {
    let resource_lock = RESOURCE_LOCK.get_or_init(|| Mutex::new(()));
    let _guard = resource_lock.lock().await;

    ensure_credentials();
    let aws_config = aws_config::defaults(aws_config::BehaviorVersion::latest())
        .region(aws_config::Region::new(config.region.clone()))
        .endpoint_url(config.endpoint_url.as_deref().expect("LocalStack endpoint"))
        .load()
        .await;
    let s3 = aws_sdk_s3::Client::from_conf(
        aws_sdk_s3::config::Builder::from(&aws_config)
            .force_path_style(true)
            .build(),
    );
    if s3
        .head_bucket()
        .bucket(&config.s3_bucket)
        .send()
        .await
        .is_err()
    {
        if let Err(error) = s3.create_bucket().bucket(&config.s3_bucket).send().await {
            if s3
                .head_bucket()
                .bucket(&config.s3_bucket)
                .send()
                .await
                .is_err()
            {
                panic!("create LocalStack S3 bucket: {error:?}");
            }
        }
    }

    let dynamodb = aws_sdk_dynamodb::Client::new(&aws_config);
    create_table(
        &dynamodb,
        &config.dynamodb_table,
        vec![("id", ScalarAttributeType::S)],
        vec![("id", KeyType::Hash)],
    )
    .await;
    create_table(
        &dynamodb,
        &config.dynamodb_folders_table,
        vec![("id", ScalarAttributeType::S)],
        vec![("id", KeyType::Hash)],
    )
    .await;
    create_table(
        &dynamodb,
        &config.dynamodb_versions_table,
        vec![
            ("file_id", ScalarAttributeType::S),
            ("version", ScalarAttributeType::N),
        ],
        vec![("file_id", KeyType::Hash), ("version", KeyType::Range)],
    )
    .await;
    create_table(
        &dynamodb,
        &config.dynamodb_shares_table,
        vec![("id", ScalarAttributeType::S)],
        vec![("id", KeyType::Hash)],
    )
    .await;
}

async fn create_table(
    client: &aws_sdk_dynamodb::Client,
    table_name: &str,
    attributes: Vec<(&str, ScalarAttributeType)>,
    keys: Vec<(&str, KeyType)>,
) {
    if client
        .describe_table()
        .table_name(table_name)
        .send()
        .await
        .is_err()
    {
        let mut builder = client
            .create_table()
            .table_name(table_name)
            .billing_mode(aws_sdk_dynamodb::types::BillingMode::PayPerRequest);
        for (name, attribute_type) in attributes {
            builder = builder.attribute_definitions(
                AttributeDefinition::builder()
                    .attribute_name(name)
                    .attribute_type(attribute_type)
                    .build()
                    .expect("valid attribute definition"),
            );
        }
        for (name, key_type) in keys {
            builder = builder.key_schema(
                KeySchemaElement::builder()
                    .attribute_name(name)
                    .key_type(key_type)
                    .build()
                    .expect("valid key schema"),
            );
        }
        if let Err(error) = builder.send().await {
            if client
                .describe_table()
                .table_name(table_name)
                .send()
                .await
                .is_err()
            {
                panic!("create LocalStack DynamoDB table {table_name}: {error:?}");
            }
        }
    }

    for _ in 0..100 {
        if let Ok(table) = client.describe_table().table_name(table_name).send().await {
            if table
                .table
                .as_ref()
                .and_then(|table| table.table_status.as_ref())
                == Some(&TableStatus::Active)
            {
                return;
            }
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    panic!("LocalStack DynamoDB table {table_name} did not become active");
}

fn ensure_credentials() {
    CREDENTIALS.call_once(|| {
        std::env::set_var("AWS_ACCESS_KEY_ID", "test");
        std::env::set_var("AWS_SECRET_ACCESS_KEY", "test");
        std::env::set_var("AWS_SESSION_TOKEN", "test");
        std::env::set_var("AWS_EC2_METADATA_DISABLED", "true");
    });
}

#[allow(dead_code)]
pub fn bytes(value: &[u8]) -> Bytes {
    Bytes::copy_from_slice(value)
}

//! Hermetic test doubles shared by the unit tests: AWS SDK clients backed by a
//! static HTTP replay client, and a minimal in-process RESP server standing in
//! for Redis. Nothing here touches the network beyond loopback.

use actix_web::web;
use aws_smithy_http_client::test_util::{ReplayEvent, StaticReplayClient};
use aws_smithy_types::body::SdkBody;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{tcp::OwnedReadHalf, TcpListener, TcpStream};

use crate::config::{AppConfig, AwsConfig, ServerConfig, SnsConfig};
use crate::events::EventPublisher;
use crate::metadata::MetadataClient;
use crate::storage::S3Client;

pub const TEST_BUCKET: &str = "otterworks-files-test";

// -- HTTP response builders -------------------------------------------------

pub fn http_response(status: u16, body: &str) -> http::Response<SdkBody> {
    http::Response::builder()
        .status(status)
        .body(SdkBody::from(body))
        .unwrap()
}

pub fn expected_request(uri: &str) -> http::Request<SdkBody> {
    http::Request::builder()
        .uri(uri)
        .body(SdkBody::empty())
        .unwrap()
}

/// An empty 200, as S3 returns for PutObject / DeleteObject.
pub fn s3_ok() -> http::Response<SdkBody> {
    http_response(200, "")
}

pub fn s3_body(content: &str) -> http::Response<SdkBody> {
    http_response(200, content)
}

/// An S3 REST-XML error, e.g. `s3_error(404, "NoSuchBucket")`.
pub fn s3_error(status: u16, code: &str) -> http::Response<SdkBody> {
    http_response(
        status,
        &format!(
            r#"<?xml version="1.0" encoding="UTF-8"?><Error><Code>{code}</Code><Message>{code}</Message></Error>"#
        ),
    )
}

/// A DynamoDB awsJson1_0 success body.
pub fn dynamo_ok(body: &str) -> http::Response<SdkBody> {
    http_response(200, body)
}

/// A DynamoDB `GetItem` response with no `Item`, i.e. key not present.
pub fn dynamo_missing_item() -> http::Response<SdkBody> {
    dynamo_ok("{}")
}

pub fn dynamo_error(code: &str) -> http::Response<SdkBody> {
    http::Response::builder()
        .status(400)
        .header("x-amzn-errortype", code)
        .body(SdkBody::from(format!(
            r#"{{"__type":"com.amazonaws.dynamodb.v20120810#{code}","message":"{code}"}}"#
        )))
        .unwrap()
}

// -- SDK clients backed by the replay client --------------------------------

fn replay(events: Vec<ReplayEvent>) -> StaticReplayClient {
    StaticReplayClient::new(events)
}

pub fn s3_client(events: Vec<ReplayEvent>) -> (S3Client, StaticReplayClient) {
    let http_client = replay(events);
    let conf = aws_sdk_s3::config::Builder::new()
        .behavior_version(aws_sdk_s3::config::BehaviorVersion::latest())
        .region(aws_sdk_s3::config::Region::new("us-east-1"))
        .credentials_provider(aws_sdk_s3::config::Credentials::new(
            "test-key",
            "test-secret",
            None,
            None,
            "test",
        ))
        .retry_config(aws_sdk_s3::config::retry::RetryConfig::disabled())
        .http_client(http_client.clone())
        .force_path_style(true)
        .endpoint_url("http://s3.local")
        .build();

    let client = S3Client {
        client: aws_sdk_s3::Client::from_conf(conf),
        bucket: TEST_BUCKET.to_string(),
    };
    (client, http_client)
}

pub fn metadata_client(events: Vec<ReplayEvent>) -> (MetadataClient, StaticReplayClient) {
    let http_client = replay(events);
    let conf = aws_sdk_dynamodb::config::Builder::new()
        .behavior_version(aws_sdk_dynamodb::config::BehaviorVersion::latest())
        .region(aws_sdk_dynamodb::config::Region::new("us-east-1"))
        .credentials_provider(aws_sdk_dynamodb::config::Credentials::new(
            "test-key",
            "test-secret",
            None,
            None,
            "test",
        ))
        .retry_config(aws_sdk_dynamodb::config::retry::RetryConfig::disabled())
        .http_client(http_client.clone())
        .endpoint_url("http://dynamodb.local")
        .build();

    let client = MetadataClient {
        client: aws_sdk_dynamodb::Client::from_conf(conf),
        files_table: "files".into(),
        folders_table: "folders".into(),
        versions_table: "versions".into(),
        shares_table: "shares".into(),
    };
    (client, http_client)
}

pub fn sns_client(events: Vec<ReplayEvent>) -> (aws_sdk_sns::Client, StaticReplayClient) {
    let http_client = replay(events);
    let conf = aws_sdk_sns::config::Builder::new()
        .behavior_version(aws_sdk_sns::config::BehaviorVersion::latest())
        .region(aws_sdk_sns::config::Region::new("us-east-1"))
        .credentials_provider(aws_sdk_sns::config::Credentials::new(
            "test-key",
            "test-secret",
            None,
            None,
            "test",
        ))
        .retry_config(aws_sdk_sns::config::retry::RetryConfig::disabled())
        .http_client(http_client.clone())
        .endpoint_url("http://sns.local")
        .build();

    (aws_sdk_sns::Client::from_conf(conf), http_client)
}

/// An `EventPublisher` with no topic configured: `publish` short-circuits, so no
/// HTTP call is ever made.
pub fn silent_event_publisher() -> EventPublisher {
    let (client, _) = sns_client(vec![]);
    EventPublisher::from_parts(client, None)
}

pub fn test_config(max_upload_bytes: u64) -> AppConfig {
    AppConfig {
        server: ServerConfig {
            port: 8082,
            max_upload_bytes,
        },
        aws: AwsConfig {
            region: "us-east-1".into(),
            endpoint_url: Some("http://s3.local".into()),
            s3_bucket: TEST_BUCKET.into(),
            dynamodb_table: "files".into(),
            dynamodb_folders_table: "folders".into(),
            dynamodb_versions_table: "versions".into(),
            dynamodb_shares_table: "shares".into(),
        },
        sns: SnsConfig { topic_arn: None },
    }
}

/// The `app_data` set that `main` installs, wired to test doubles.
pub struct TestState {
    pub s3: web::Data<S3Client>,
    pub meta: web::Data<MetadataClient>,
    pub events: web::Data<EventPublisher>,
    pub config: web::Data<AppConfig>,
    pub redis: web::Data<redis::aio::ConnectionManager>,
}

impl TestState {
    pub fn register(&self, cfg: &mut web::ServiceConfig) {
        cfg.app_data(self.s3.clone())
            .app_data(self.meta.clone())
            .app_data(self.events.clone())
            .app_data(self.config.clone())
            .app_data(self.redis.clone());
        crate::handlers::configure_routes(cfg);
    }
}

/// Build the handler dependencies. `chaos_flags` are the Redis keys the fake
/// server reports as present, which is what `chaos_active` probes with EXISTS.
pub async fn test_state(
    s3_events: Vec<ReplayEvent>,
    dynamo_events: Vec<ReplayEvent>,
    chaos_flags: &[&str],
) -> (TestState, StaticReplayClient, StaticReplayClient) {
    let (s3, s3_http) = s3_client(s3_events);
    let (meta, dynamo_http) = metadata_client(dynamo_events);
    let redis = fake_redis_connection(chaos_flags).await;

    let state = TestState {
        s3: web::Data::new(s3),
        meta: web::Data::new(meta),
        events: web::Data::new(silent_event_publisher()),
        config: web::Data::new(test_config(1024 * 1024)),
        redis: web::Data::new(redis),
    };
    (state, s3_http, dynamo_http)
}

// -- Multipart bodies -------------------------------------------------------

pub const BOUNDARY: &str = "otterworksboundary";

pub fn multipart_content_type() -> String {
    format!("multipart/form-data; boundary={BOUNDARY}")
}

/// Build a multipart upload body: an optional `file` part plus text parts.
pub fn multipart_body(file: Option<(&str, &str, &[u8])>, text_fields: &[(&str, &str)]) -> Vec<u8> {
    let mut body = Vec::new();
    if let Some((name, content_type, content)) = file {
        body.extend_from_slice(
            format!(
                "--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: {content_type}\r\n\r\n"
            )
            .as_bytes(),
        );
        body.extend_from_slice(content);
        body.extend_from_slice(b"\r\n");
    }
    for (name, value) in text_fields {
        body.extend_from_slice(
            format!(
                "--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n"
            )
            .as_bytes(),
        );
    }
    body.extend_from_slice(format!("--{BOUNDARY}--\r\n").as_bytes());
    body
}

// -- Fake Redis -------------------------------------------------------------

/// Start a loopback RESP server that answers `EXISTS` from `present_keys` and
/// `+OK` to everything else (the connection handshake), and connect to it.
pub async fn fake_redis_connection(present_keys: &[&str]) -> redis::aio::ConnectionManager {
    let keys: Vec<String> = present_keys.iter().map(|k| k.to_string()).collect();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    tokio::spawn(async move {
        while let Ok((stream, _)) = listener.accept().await {
            let keys = keys.clone();
            tokio::spawn(serve_resp(stream, keys));
        }
    });

    let client = redis::Client::open(format!("redis://{addr}")).unwrap();
    redis::aio::ConnectionManager::new(client).await.unwrap()
}

async fn serve_resp(stream: TcpStream, present_keys: Vec<String>) {
    let (read_half, mut write_half) = stream.into_split();
    let mut reader = BufReader::new(read_half);

    while let Some(args) = read_command(&mut reader).await {
        let Some(name) = args.first() else { continue };
        let reply = match name.to_uppercase().as_str() {
            "EXISTS" => {
                let found = args[1..]
                    .iter()
                    .filter(|key| present_keys.contains(key))
                    .count();
                format!(":{found}\r\n")
            }
            _ => "+OK\r\n".to_string(),
        };
        if write_half.write_all(reply.as_bytes()).await.is_err() {
            return;
        }
    }
}

async fn read_command(reader: &mut BufReader<OwnedReadHalf>) -> Option<Vec<String>> {
    let mut line = String::new();
    if reader.read_line(&mut line).await.ok()? == 0 {
        return None;
    }
    let argc: usize = line.trim_start_matches('*').trim().parse().ok()?;

    let mut args = Vec::with_capacity(argc);
    for _ in 0..argc {
        let mut header = String::new();
        reader.read_line(&mut header).await.ok()?;
        let len: usize = header.trim_start_matches('$').trim().parse().ok()?;
        let mut buf = vec![0u8; len + 2]; // payload + CRLF
        reader.read_exact(&mut buf).await.ok()?;
        buf.truncate(len);
        args.push(String::from_utf8(buf).ok()?);
    }
    Some(args)
}

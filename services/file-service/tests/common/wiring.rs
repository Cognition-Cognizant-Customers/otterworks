//! Wiring that assembles the real handlers against the mock AWS endpoint and the RESP
//! stub. Kept out of `common/mod.rs` so that test binaries which only need the harness
//! (e.g. `storage_it`) do not have to pull in every source module.

use std::sync::{Arc, Mutex};

use actix_web::web;
use serde_json::Value;
use uuid::Uuid;

use crate::common::{
    dynamodb_sdk_client, s3_sdk_client, state_responder, FakeRedis, MockAws, MockState, Responder,
    FILES_TABLE, FOLDERS_TABLE, SHARES_TABLE, TEST_BUCKET, VERSIONS_TABLE,
};
use crate::config::{AppConfig, AwsConfig, ServerConfig, SnsConfig};
use crate::events::EventPublisher;
use crate::metadata::MetadataClient;
use crate::storage::S3Client;

/// Default upload ceiling used by the boundary tests. Small on purpose: the handler
/// compares against `config.server.max_upload_bytes`, so the branch is identical to the
/// 100 MB production default while keeping the suite fast.
pub const TEST_MAX_UPLOAD_BYTES: u64 = 1024;

/// Production default, `src/config.rs`: `MAX_UPLOAD_BYTES` → 100 MiB.
pub const PROD_MAX_UPLOAD_BYTES: u64 = 104_857_600;

pub fn test_aws_config(endpoint: &str) -> AwsConfig {
    AwsConfig {
        region: "us-east-1".into(),
        endpoint_url: Some(endpoint.to_string()),
        s3_bucket: TEST_BUCKET.into(),
        dynamodb_table: FILES_TABLE.into(),
        dynamodb_folders_table: FOLDERS_TABLE.into(),
        dynamodb_versions_table: VERSIONS_TABLE.into(),
        dynamodb_shares_table: SHARES_TABLE.into(),
    }
}

pub fn test_app_config(endpoint: &str, max_upload_bytes: u64) -> AppConfig {
    AppConfig {
        server: ServerConfig {
            port: 0,
            max_upload_bytes,
        },
        aws: test_aws_config(endpoint),
        sns: SnsConfig { topic_arn: None },
    }
}

pub fn test_s3_client(endpoint: &str, bucket: &str) -> S3Client {
    S3Client {
        client: s3_sdk_client(endpoint),
        bucket: bucket.to_string(),
    }
}

pub fn test_metadata_client(endpoint: &str) -> MetadataClient {
    MetadataClient {
        client: dynamodb_sdk_client(endpoint),
        files_table: FILES_TABLE.into(),
        folders_table: FOLDERS_TABLE.into(),
        versions_table: VERSIONS_TABLE.into(),
        shares_table: SHARES_TABLE.into(),
    }
}

/// A fully wired, fully offline file-service: mock AWS, stub Redis, real handlers.
pub struct TestEnv {
    pub aws: MockAws,
    pub redis: FakeRedis,
    pub state: Arc<Mutex<MockState>>,
    pub s3: web::Data<S3Client>,
    pub meta: web::Data<MetadataClient>,
    pub events: web::Data<EventPublisher>,
    pub config: web::Data<AppConfig>,
    pub redis_cm: web::Data<redis::aio::ConnectionManager>,
}

pub struct TestEnvBuilder {
    max_upload_bytes: u64,
    chaos_flag_set: bool,
    responder: Option<Box<dyn FnOnce(Responder) -> Responder>>,
}

impl TestEnvBuilder {
    pub fn max_upload_bytes(mut self, value: u64) -> Self {
        self.max_upload_bytes = value;
        self
    }

    pub fn chaos_flag_set(mut self, value: bool) -> Self {
        self.chaos_flag_set = value;
        self
    }

    /// Wrap the state-backed responder, e.g. to inject an S3 or DynamoDB failure.
    pub fn responder(mut self, wrap: impl FnOnce(Responder) -> Responder + 'static) -> Self {
        self.responder = Some(Box::new(wrap));
        self
    }

    pub async fn build(self) -> TestEnv {
        let state = MockState::shared();
        let mut responder = state_responder(state.clone());
        if let Some(wrap) = self.responder {
            responder = wrap(responder);
        }
        let aws = MockAws::start(responder).await;
        let redis = FakeRedis::start(self.chaos_flag_set).await;
        let endpoint = aws.endpoint();

        let config = test_app_config(&endpoint, self.max_upload_bytes);
        let s3 = test_s3_client(&endpoint, &config.aws.s3_bucket);
        let meta = test_metadata_client(&endpoint);
        let events = EventPublisher::new(&config.sns, &config.aws).await;
        let redis_cm = redis.connection_manager().await;

        TestEnv {
            aws,
            redis,
            state,
            s3: web::Data::new(s3),
            meta: web::Data::new(meta),
            events: web::Data::new(events),
            config: web::Data::new(config),
            redis_cm: web::Data::new(redis_cm),
        }
    }
}

impl TestEnv {
    pub fn builder() -> TestEnvBuilder {
        TestEnvBuilder {
            max_upload_bytes: TEST_MAX_UPLOAD_BYTES,
            chaos_flag_set: false,
            responder: None,
        }
    }

    pub async fn new() -> Self {
        Self::builder().build().await
    }

    pub fn seed_file(&self, item: Value) {
        self.state.lock().expect("state lock").files.push(item);
    }

    pub fn seed_version(&self, item: Value) {
        self.state.lock().expect("state lock").versions.push(item);
    }

    pub fn seed_share(&self, item: Value) {
        self.state.lock().expect("state lock").shares.push(item);
    }

    pub fn seed_folder(&self, item: Value) {
        self.state.lock().expect("state lock").folders.push(item);
    }

    /// The stored file row, as the mock DynamoDB currently holds it.
    pub fn stored_file(&self, id: Uuid) -> Option<Value> {
        self.state
            .lock()
            .expect("state lock")
            .files
            .iter()
            .find(|item| item["id"]["S"].as_str() == Some(id.to_string().as_str()))
            .cloned()
    }

    pub fn stored_share_count(&self) -> usize {
        self.state.lock().expect("state lock").shares.len()
    }

    pub fn stored_folder_count(&self) -> usize {
        self.state.lock().expect("state lock").folders.len()
    }

    /// The same route table `main.rs` installs, minus the observability middleware.
    pub fn configure(&self, cfg: &mut web::ServiceConfig) {
        cfg.app_data(self.config.clone())
            .app_data(self.s3.clone())
            .app_data(self.meta.clone())
            .app_data(self.events.clone())
            .app_data(self.redis_cm.clone())
            .route("/health", web::get().to(crate::handlers::health))
            .service(
                web::scope("/api/v1/files")
                    .route("/upload", web::post().to(crate::handlers::upload_file))
                    .route("/shared", web::get().to(crate::handlers::list_shared_files))
                    .route("/trash", web::get().to(crate::handlers::list_trashed))
                    .route("/activity", web::get().to(crate::handlers::list_activity))
                    .route("", web::get().to(crate::handlers::list_files))
                    .route(
                        "/{file_id}",
                        web::get().to(crate::handlers::get_file_metadata),
                    )
                    .route("/{file_id}", web::delete().to(crate::handlers::delete_file))
                    .route(
                        "/{file_id}/download",
                        web::get().to(crate::handlers::download_file),
                    )
                    .route("/{file_id}/move", web::put().to(crate::handlers::move_file))
                    .route(
                        "/{file_id}/rename",
                        web::patch().to(crate::handlers::rename_file),
                    )
                    .route(
                        "/{file_id}/versions",
                        web::get().to(crate::handlers::list_versions),
                    )
                    .route(
                        "/{file_id}/trash",
                        web::post().to(crate::handlers::trash_file),
                    )
                    .route(
                        "/{file_id}/restore",
                        web::post().to(crate::handlers::restore_file),
                    )
                    .route(
                        "/{file_id}/share",
                        web::post().to(crate::handlers::share_file),
                    )
                    .route(
                        "/{file_id}/share/{user_id}",
                        web::delete().to(crate::handlers::remove_share),
                    ),
            )
            .service(
                web::scope("/api/v1/folders")
                    .route("", web::get().to(crate::handlers::list_folders))
                    .route("", web::post().to(crate::handlers::create_folder))
                    .route("/{folder_id}", web::get().to(crate::handlers::get_folder))
                    .route(
                        "/{folder_id}",
                        web::put().to(crate::handlers::update_folder),
                    )
                    .route(
                        "/{folder_id}",
                        web::delete().to(crate::handlers::delete_folder),
                    ),
            );
    }
}

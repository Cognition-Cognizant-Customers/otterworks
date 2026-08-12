use actix_web::{http::StatusCode, test as actix_test, web, App};
use aws_credential_types::Credentials;
use aws_smithy_http_client::test_util::{ReplayEvent, StaticReplayClient};
use aws_smithy_runtime_api::{
    client::orchestrator::{HttpRequest, HttpResponse},
    http::{Request, Response, StatusCode as SmithyStatusCode},
};
use aws_smithy_types::body::SdkBody;
use file_service::{
    config::{AppConfig, AwsConfig, ServerConfig, SnsConfig},
    events::EventPublisher,
    handlers,
    metadata::MetadataClient,
    models::SharePermission,
    storage::S3Client,
};
use serde_json::{json, Value};
use uuid::Uuid;

fn req() -> HttpRequest {
    Request::get("http://test").unwrap()
}
fn resp(body: Value) -> HttpResponse {
    let mut response = Response::new(
        SmithyStatusCode::try_from(200).unwrap(),
        SdkBody::from(serde_json::to_vec(&body).unwrap()),
    );
    response
        .headers_mut()
        .insert("content-type", "application/x-amz-json-1.0");
    response
}
fn empty_response() -> HttpResponse {
    Response::new(SmithyStatusCode::try_from(200).unwrap(), SdkBody::empty())
}
fn replay(events: Vec<ReplayEvent>) -> StaticReplayClient {
    StaticReplayClient::new(events)
}
fn file(id: Uuid, owner: Uuid) -> Value {
    json!({"id":{"S":id.to_string()},"name":{"S":"report.txt"},"mime_type":{"S":"text/plain"},
        "size_bytes":{"N":"5"},"s3_key":{"S":format!("files/{owner}/{id}")},"owner_id":{"S":owner.to_string()},
        "version":{"N":"1"},"is_trashed":{"BOOL":false},"created_at":{"S":"2024-01-01T00:00:00Z"},
        "updated_at":{"S":"2024-01-01T00:00:00Z"}})
}
fn clients(
    s3_replay: StaticReplayClient,
    db_replay: StaticReplayClient,
) -> (
    web::Data<S3Client>,
    web::Data<MetadataClient>,
    web::Data<EventPublisher>,
    web::Data<AppConfig>,
) {
    let region = aws_config::Region::new("us-east-1");
    let credentials = Credentials::new("test", "test", None, None, "test");
    let s3 = aws_sdk_s3::Client::from_conf(
        aws_sdk_s3::Config::builder()
            .behavior_version(aws_sdk_s3::config::BehaviorVersion::latest())
            .region(region.clone())
            .credentials_provider(credentials.clone())
            .http_client(s3_replay)
            .build(),
    );
    let db = aws_sdk_dynamodb::Client::from_conf(
        aws_sdk_dynamodb::Config::builder()
            .behavior_version(aws_sdk_dynamodb::config::BehaviorVersion::latest())
            .region(region.clone())
            .credentials_provider(credentials.clone())
            .http_client(db_replay)
            .build(),
    );
    let sns = aws_sdk_sns::Client::from_conf(
        aws_sdk_sns::Config::builder()
            .behavior_version(aws_sdk_sns::config::BehaviorVersion::latest())
            .region(region)
            .credentials_provider(credentials)
            .http_client(replay(vec![]))
            .build(),
    );
    (
        web::Data::new(S3Client::from_client(s3, "files")),
        web::Data::new(MetadataClient::from_client(
            db, "files", "folders", "versions", "shares",
        )),
        web::Data::new(EventPublisher::from_client(sns, None)),
        web::Data::new(AppConfig {
            server: ServerConfig {
                port: 8082,
                max_upload_bytes: 1024,
            },
            aws: AwsConfig {
                region: "us-east-1".into(),
                endpoint_url: None,
                s3_bucket: "files".into(),
                dynamodb_table: "files".into(),
                dynamodb_folders_table: "folders".into(),
                dynamodb_versions_table: "versions".into(),
                dynamodb_shares_table: "shares".into(),
            },
            sns: SnsConfig { topic_arn: None },
        }),
    )
}

#[actix_web::test]
async fn health_and_metrics_return_contracts() {
    let app = actix_test::init_service(
        App::new()
            .route("/health", web::get().to(handlers::health))
            .route("/metrics", web::get().to(handlers::metrics)),
    )
    .await;
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::get().uri("/health").to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::OK);
    let body: Value = actix_test::read_body_json(r).await;
    assert_eq!(body["status"], "healthy");
    assert_eq!(body["service"], "file-service");
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::get().uri("/metrics").to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::OK);
    assert!(r
        .headers()
        .get("content-type")
        .unwrap()
        .to_str()
        .unwrap()
        .contains("text/plain"));
}

#[actix_web::test]
async fn upload_happy_path_and_size_rejection() {
    let owner = Uuid::new_v4();
    let s3_mock = replay(vec![ReplayEvent::new(req(), empty_response())]);
    let db_mock = replay(vec![
        ReplayEvent::new(req(), resp(json!({}))),
        ReplayEvent::new(req(), resp(json!({}))),
    ]);
    let (s3, db, events, config) = clients(s3_mock, db_mock);
    let app = actix_test::init_service(
        App::new()
            .app_data(s3)
            .app_data(db)
            .app_data(events)
            .app_data(config)
            .route("/upload", web::post().to(handlers::upload_file)),
    )
    .await;
    let r = actix_test::call_service(&app, actix_test::TestRequest::post().uri("/upload")
        .insert_header(("x-user-id", owner.to_string()))
        .insert_header(("content-type", "multipart/form-data; boundary=b"))
        .set_payload("--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"report.txt\"\r\nContent-Type: text/plain\r\n\r\nhello\r\n--b--\r\n").to_request()).await;
    assert_eq!(r.status(), StatusCode::CREATED);
    let body: Value = actix_test::read_body_json(r).await;
    assert_eq!(body["file"]["name"], "report.txt");
    assert_eq!(body["file"]["size_bytes"], 5);
    let (s3, db, events, base_config) = clients(replay(vec![]), replay(vec![]));
    let config = web::Data::new(AppConfig {
        server: ServerConfig {
            max_upload_bytes: 4,
            ..base_config.server.clone()
        },
        ..base_config.as_ref().clone()
    });
    let app = actix_test::init_service(
        App::new()
            .app_data(s3)
            .app_data(db)
            .app_data(events)
            .app_data(config)
            .route("/upload", web::post().to(handlers::upload_file)),
    )
    .await;
    let r = actix_test::call_service(&app, actix_test::TestRequest::post().uri("/upload")
        .insert_header(("x-user-id", owner.to_string()))
        .insert_header(("content-type", "multipart/form-data; boundary=b"))
        .set_payload("--b\r\nContent-Disposition: form-data; name=\"file\"; filename=\"x\"\r\n\r\nhello\r\n--b--\r\n").to_request()).await;
    assert_eq!(r.status(), StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(
        actix_test::read_body_json::<Value, _>(r).await["error"],
        "file_too_large"
    );
}

#[actix_web::test]
async fn list_get_and_get_not_found_use_dynamo_replays() {
    let id = Uuid::new_v4();
    let owner = Uuid::new_v4();
    let shared = Uuid::new_v4();
    let item = file(id, owner);
    let share = json!({"id":{"S":Uuid::new_v4().to_string()},"file_id":{"S":id.to_string()},
        "shared_with":{"S":shared.to_string()},"permission":{"S":"viewer"},"shared_by":{"S":owner.to_string()},
        "created_at":{"S":"2024-01-01T00:00:00Z"}});
    let db_mock = replay(vec![
        ReplayEvent::new(req(), resp(json!({"Items":[item.clone()]}))),
        ReplayEvent::new(req(), resp(json!({"Item":item.clone()}))),
        ReplayEvent::new(req(), resp(json!({"Items":[share]}))),
        ReplayEvent::new(req(), resp(json!({}))),
    ]);
    let (s3, db, events, _) = clients(replay(vec![]), db_mock);
    let app = actix_test::init_service(
        App::new()
            .app_data(s3)
            .app_data(db)
            .app_data(events)
            .route("/files", web::get().to(handlers::list_files))
            .route("/files/{id}", web::get().to(handlers::get_file_metadata)),
    )
    .await;
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::get()
            .uri(&format!("/files?owner_id={owner}"))
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::OK);
    assert_eq!(
        actix_test::read_body_json::<Value, _>(r).await["files"][0]["id"],
        id.to_string()
    );
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::get()
            .uri(&format!("/files/{id}"))
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::OK);
    let body: Value = actix_test::read_body_json(r).await;
    assert_eq!(body["name"], "report.txt");
    assert_eq!(body["shared_with"][0]["shared_with"], shared.to_string());
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::get()
            .uri(&format!("/files/{}", Uuid::new_v4()))
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::NOT_FOUND);
    assert_eq!(
        actix_test::read_body_json::<Value, _>(r).await["error"],
        "file_not_found"
    );
}

#[actix_web::test]
async fn delete_folder_versions_and_shares_assert_responses() {
    let id = Uuid::new_v4();
    let owner = Uuid::new_v4();
    let folder_id = Uuid::new_v4();
    let folder = json!({"id":{"S":folder_id.to_string()},"name":{"S":"Docs"},"owner_id":{"S":owner.to_string()},
        "created_at":{"S":"2024-01-01T00:00:00Z"},"updated_at":{"S":"2024-01-01T00:00:00Z"}});
    let db_mock = replay(vec![
        ReplayEvent::new(req(), resp(json!({"Items":[folder.clone()]}))),
        ReplayEvent::new(req(), resp(json!({}))),
        ReplayEvent::new(req(), resp(json!({"Item":folder.clone()}))),
        ReplayEvent::new(
            req(),
            resp(
                json!({"Count":1,"Items":[{"file_id":{"S":id.to_string()},"version":{"N":"2"},"s3_key":{"S":"v2"},"size_bytes":{"N":"9"},"created_by":{"S":owner.to_string()},"created_at":{"S":"2024-01-01T00:00:00Z"}}]}),
            ),
        ),
        ReplayEvent::new(req(), resp(json!({"Item":file(id, owner)}))),
        ReplayEvent::new(req(), resp(json!({"Items":[]}))),
        ReplayEvent::new(req(), resp(json!({}))),
    ]);
    let s3_mock = replay(vec![ReplayEvent::new(req(), empty_response())]);
    let (s3, db, events, _) = clients(s3_mock, db_mock);
    let app = actix_test::init_service(
        App::new()
            .app_data(s3)
            .app_data(db)
            .app_data(events)
            .route("/folders", web::get().to(handlers::list_folders))
            .route("/folders/{id}", web::delete().to(handlers::delete_folder))
            .route("/files/{id}", web::delete().to(handlers::delete_file))
            .route(
                "/files/{id}/versions",
                web::get().to(handlers::list_versions),
            )
            .route("/files/{id}/share", web::post().to(handlers::share_file)),
    )
    .await;
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::get()
            .uri(&format!("/folders?owner_id={owner}"))
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::OK);
    assert_eq!(
        actix_test::read_body_json::<Value, _>(r).await["folders"][0]["name"],
        "Docs"
    );
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::delete()
            .uri(&format!("/folders/{folder_id}"))
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::NO_CONTENT);
}

#[actix_web::test]
async fn versions_return_decoded_items() {
    let id = Uuid::new_v4();
    let owner = Uuid::new_v4();
    let db_mock = replay(vec![ReplayEvent::new(
        req(),
        resp(json!({"Count":1,"Items":[{
            "file_id":{"S":id.to_string()},"version":{"N":"2"},"s3_key":{"S":"v2"},
            "size_bytes":{"N":"9"},"created_by":{"S":owner.to_string()},
            "created_at":{"S":"2024-01-01T00:00:00Z"}
        }]})),
    )]);
    let (s3, db, events, _) = clients(replay(vec![]), db_mock);
    let app =
        actix_test::init_service(App::new().app_data(s3).app_data(db).app_data(events).route(
            "/files/{id}/versions",
            web::get().to(handlers::list_versions),
        ))
        .await;
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::get()
            .uri(&format!("/files/{id}/versions"))
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::OK);
    let body: Value = actix_test::read_body_json(r).await;
    assert_eq!(body["versions"][0]["version"], 2);
}

#[actix_web::test]
async fn delete_file_removes_metadata_and_blob() {
    let id = Uuid::new_v4();
    let owner = Uuid::new_v4();
    let db_mock = replay(vec![
        ReplayEvent::new(req(), resp(json!({"Item": file(id, owner)}))),
        ReplayEvent::new(req(), resp(json!({}))),
    ]);
    let s3_mock = replay(vec![ReplayEvent::new(req(), empty_response())]);
    let (s3, db, events, _) = clients(s3_mock, db_mock);
    let app = actix_test::init_service(
        App::new()
            .app_data(s3)
            .app_data(db)
            .app_data(events)
            .route("/files/{id}", web::delete().to(handlers::delete_file)),
    )
    .await;
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::delete()
            .uri(&format!("/files/{id}"))
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::NO_CONTENT);
}

#[actix_web::test]
async fn share_file_creates_share_with_expected_payload() {
    let id = Uuid::new_v4();
    let owner = Uuid::new_v4();
    let recipient = Uuid::new_v4();
    let db_mock = replay(vec![
        ReplayEvent::new(req(), resp(json!({"Item": file(id, owner)}))),
        ReplayEvent::new(req(), resp(json!({"Items": []}))),
        ReplayEvent::new(req(), resp(json!({}))),
    ]);
    let (s3, db, events, _) = clients(replay(vec![]), db_mock);
    let app = actix_test::init_service(
        App::new()
            .app_data(s3)
            .app_data(db)
            .app_data(events)
            .route("/files/{id}/share", web::post().to(handlers::share_file)),
    )
    .await;
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::post()
            .uri(&format!("/files/{id}/share"))
            .insert_header(("content-type", "application/json"))
            .set_payload(format!(
                r#"{{"shared_with":"{recipient}","permission":"editor","shared_by":"{owner}"}}"#
            ))
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::CREATED);
    let body: Value = actix_test::read_body_json(r).await;
    assert_eq!(body["share"]["file_id"], id.to_string());
    assert_eq!(body["share"]["shared_with"], recipient.to_string());
    assert_eq!(body["share"]["permission"], "editor");
}

#[actix_web::test]
async fn create_and_update_folder_return_metadata() {
    let owner = Uuid::new_v4();
    let folder_id = Uuid::new_v4();
    let folder = json!({
        "id": {"S": folder_id.to_string()}, "name": {"S": "Docs"},
        "owner_id": {"S": owner.to_string()},
        "created_at": {"S": "2024-01-01T00:00:00Z"},
        "updated_at": {"S": "2024-01-01T00:00:00Z"}
    });
    let db_mock = replay(vec![
        ReplayEvent::new(req(), resp(json!({}))),
        ReplayEvent::new(req(), resp(json!({}))),
        ReplayEvent::new(req(), resp(json!({"Item": folder}))),
    ]);
    let (s3, db, events, _) = clients(replay(vec![]), db_mock);
    let app = actix_test::init_service(
        App::new()
            .app_data(s3)
            .app_data(db)
            .app_data(events)
            .route("/folders", web::post().to(handlers::create_folder))
            .route("/folders/{id}", web::put().to(handlers::update_folder)),
    )
    .await;
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::post()
            .uri("/folders")
            .insert_header(("content-type", "application/json"))
            .set_payload(format!(r#"{{"name":"Docs","owner_id":"{owner}"}}"#))
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::CREATED);
    let created: Value = actix_test::read_body_json(r).await;
    assert_eq!(created["name"], "Docs");
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::put()
            .uri(&format!("/folders/{folder_id}"))
            .insert_header(("content-type", "application/json"))
            .set_payload(r#"{"name":"Renamed"}"#)
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::OK);
    assert_eq!(
        actix_test::read_body_json::<Value, _>(r).await["name"],
        "Docs"
    );
}

#[actix_web::test]
async fn malformed_folder_put_and_share_remove_are_rejected() {
    let (s3, db, events, _) = clients(replay(vec![]), replay(vec![]));
    let app = actix_test::init_service(
        App::new()
            .app_data(s3)
            .app_data(db)
            .app_data(events)
            .route("/folders/{id}", web::put().to(handlers::update_folder))
            .route(
                "/files/{id}/share/{user}",
                web::delete().to(handlers::remove_share),
            ),
    )
    .await;
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::put()
            .uri("/folders/bad")
            .insert_header(("content-type", "application/json"))
            .set_payload(r#"{"name":"x"}"#)
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::BAD_REQUEST);
    let r = actix_test::call_service(
        &app,
        actix_test::TestRequest::delete()
            .uri("/files/bad/share/bad")
            .to_request(),
    )
    .await;
    assert_eq!(r.status(), StatusCode::BAD_REQUEST);
}

#[test]
fn share_permissions_are_case_insensitive_and_serialized_lowercase() {
    assert_eq!(
        SharePermission::from_str_value("EdItOr"),
        Some(SharePermission::Editor)
    );
    assert_eq!(
        serde_json::to_string(&SharePermission::Viewer).unwrap(),
        "\"viewer\""
    );
    assert!(SharePermission::from_str_value("owner").is_none());
}

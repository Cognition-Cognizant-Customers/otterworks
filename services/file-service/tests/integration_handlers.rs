#![cfg(feature = "integration")]

#[path = "support/mod.rs"]
mod support;

use actix_web::{
    body::MessageBody,
    dev::{Service, ServiceResponse},
    test, web, App, Error,
};
use file_service::{
    configure_routes,
    models::{FileMetadata, FileShare},
    storage::S3Client,
};
use serde_json::Value;
use support::{setup, TestContext};
use uuid::Uuid;

async fn upload_file<S, B, E>(app: &S, owner_id: Uuid, filename: &str) -> FileMetadata
where
    S: Service<actix_http::Request, Response = ServiceResponse<B>, Error = E>,
    E: std::fmt::Debug,
    B: MessageBody,
{
    let boundary = format!("integration-boundary-{}", Uuid::new_v4());
    let body = format!(
        "--{boundary}\r\n\
         Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n\
         Content-Type: text/plain\r\n\r\n\
         integration payload\r\n\
         --{boundary}--\r\n"
    );
    let response = test::call_service(
        app,
        test::TestRequest::post()
            .uri("/api/v1/files/upload")
            .insert_header(("X-User-ID", owner_id.to_string()))
            .insert_header((
                "Content-Type",
                format!("multipart/form-data; boundary={boundary}"),
            ))
            .set_payload(body)
            .to_request(),
    )
    .await;
    assert_eq!(response.status(), actix_web::http::StatusCode::CREATED);

    let body: Value = test::read_body_json(response).await;
    let file: FileMetadata =
        serde_json::from_value(body["file"].clone()).expect("uploaded file JSON");
    assert_eq!(file.owner_id, owner_id);
    assert_eq!(file.name, filename);
    file
}

fn app_data(
    context: &TestContext,
) -> (
    web::Data<file_service::config::AppConfig>,
    web::Data<S3Client>,
    web::Data<file_service::metadata::MetadataClient>,
    web::Data<file_service::events::EventPublisher>,
    web::Data<redis::aio::ConnectionManager>,
) {
    (
        web::Data::new(context.config.clone()),
        web::Data::new(context.s3.clone()),
        web::Data::new(context.metadata.clone()),
        web::Data::new(context.events.clone()),
        web::Data::new(context.redis.clone()),
    )
}

async fn init_app(
    context: &TestContext,
) -> impl Service<actix_http::Request, Response = ServiceResponse<impl MessageBody>, Error = Error>
{
    let (config, s3, metadata, events, redis) = app_data(context);
    test::init_service(
        App::new()
            .wrap(actix_web::middleware::Compress::default())
            .wrap(file_service::middleware::RequestId)
            .app_data(config)
            .app_data(s3)
            .app_data(metadata)
            .app_data(events)
            .app_data(redis)
            .configure(configure_routes),
    )
    .await
}

#[tokio::test]
async fn handlers_upload_download_and_list_versions() {
    let context = setup().await;
    let app = init_app(&context).await;
    let owner_id = Uuid::new_v4();
    let file = upload_file(&app, owner_id, "integration.txt").await;

    let download = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{}/download", file.id))
            .to_request(),
    )
    .await;
    assert_eq!(download.status(), actix_web::http::StatusCode::OK);
    let download_json: Value = test::read_body_json(download).await;
    assert!(download_json["url"]
        .as_str()
        .unwrap()
        .contains(&file.s3_key));

    let versions = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{}/versions", file.id))
            .to_request(),
    )
    .await;
    assert_eq!(versions.status(), actix_web::http::StatusCode::OK);
    let versions_json: Value = test::read_body_json(versions).await;
    assert_eq!(versions_json["versions"].as_array().unwrap().len(), 1);
    assert_eq!(versions_json["versions"][0]["version"], 1);
}

#[tokio::test]
async fn handlers_list_files_paginates_three_files() {
    let context = setup().await;
    let app = init_app(&context).await;
    let owner_id = Uuid::new_v4();

    let first = upload_file(&app, owner_id, "first.txt").await;
    let second = upload_file(&app, owner_id, "second.txt").await;
    let third = upload_file(&app, owner_id, "third.txt").await;

    let page_one = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files?page=1&page_size=2")
            .insert_header(("X-User-ID", owner_id.to_string()))
            .to_request(),
    )
    .await;
    assert_eq!(page_one.status(), actix_web::http::StatusCode::OK);
    let page_one_json: Value = test::read_body_json(page_one).await;
    assert_eq!(page_one_json["total"], 3);
    assert_eq!(page_one_json["files"].as_array().unwrap().len(), 2);

    let page_two = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files?page=2&page_size=2")
            .insert_header(("X-User-ID", owner_id.to_string()))
            .to_request(),
    )
    .await;
    assert_eq!(page_two.status(), actix_web::http::StatusCode::OK);
    let page_two_json: Value = test::read_body_json(page_two).await;
    assert_eq!(page_two_json["total"], 3);
    assert_eq!(page_two_json["files"].as_array().unwrap().len(), 1);

    for file in [first, second, third] {
        context
            .s3
            .delete_object(&file.s3_key)
            .await
            .expect("delete test object");
    }
}

#[tokio::test]
async fn handlers_share_and_remove_share() {
    let context = setup().await;
    let app = init_app(&context).await;
    let owner_id = Uuid::new_v4();
    let shared_with = Uuid::new_v4();
    let file = upload_file(&app, owner_id, "shared.txt").await;

    let share = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{}/share", file.id))
            .set_json(serde_json::json!({
                "shared_with": shared_with,
                "permission": "viewer",
                "shared_by": owner_id,
            }))
            .to_request(),
    )
    .await;
    assert_eq!(share.status(), actix_web::http::StatusCode::CREATED);
    let share_json: Value = test::read_body_json(share).await;
    let _: FileShare = serde_json::from_value(share_json["share"].clone()).expect("share JSON");

    let remove_share = test::call_service(
        &app,
        test::TestRequest::delete()
            .uri(&format!("/api/v1/files/{}/share/{}", file.id, shared_with))
            .to_request(),
    )
    .await;
    assert_eq!(
        remove_share.status(),
        actix_web::http::StatusCode::NO_CONTENT
    );
}

#[tokio::test]
async fn handlers_trash_and_restore_file() {
    let context = setup().await;
    let app = init_app(&context).await;
    let owner_id = Uuid::new_v4();
    let file = upload_file(&app, owner_id, "trash.txt").await;

    let trash = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{}/trash", file.id))
            .to_request(),
    )
    .await;
    assert_eq!(trash.status(), actix_web::http::StatusCode::OK);
    let trashed: FileMetadata = test::read_body_json(trash).await;
    assert!(trashed.is_trashed);

    let restore = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{}/restore", file.id))
            .to_request(),
    )
    .await;
    assert_eq!(restore.status(), actix_web::http::StatusCode::OK);
    let restored: FileMetadata = test::read_body_json(restore).await;
    assert!(!restored.is_trashed);
}

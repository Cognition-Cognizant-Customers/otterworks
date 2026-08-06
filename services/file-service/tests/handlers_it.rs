//! Integration tests for the file-service business handlers.
//!
//! `file-service` is a binary crate, so the source modules are pulled in with `#[path]`
//! rather than through a library target — adding a `lib.rs` would mean touching production
//! code, which this work package is not allowed to do.
//!
//! Everything runs in-process against the mock AWS endpoint and RESP stub in
//! `tests/common`: no LocalStack, no docker, no clock or ordering dependence.
#![allow(dead_code)]

#[path = "../src/config.rs"]
mod config;
#[path = "../src/errors.rs"]
mod errors;
#[path = "../src/events.rs"]
mod events;
#[path = "../src/handlers.rs"]
mod handlers;
#[path = "../src/metadata.rs"]
mod metadata;
#[path = "../src/middleware.rs"]
mod middleware;
#[path = "../src/models.rs"]
mod models;
#[path = "../src/storage.rs"]
mod storage;

mod common;
#[path = "common/wiring.rs"]
mod wiring;

use actix_web::{test, App};
use serde_json::Value;
use uuid::Uuid;

use common::{
    file_item, file_item_in_folder, folder_item, multipart_body, multipart_content_type,
    override_when, s3_error, share_item, version_item, MockState, Part,
};
use wiring::{TestEnv, PROD_MAX_UPLOAD_BYTES, TEST_MAX_UPLOAD_BYTES};

macro_rules! init_app {
    ($env:expr) => {
        test::init_service(App::new().configure(|cfg| $env.configure(cfg))).await
    };
}

fn payload_of(size: usize) -> Vec<u8> {
    vec![b'x'; size]
}

fn upload_request(body: Vec<u8>, user: Option<Uuid>) -> test::TestRequest {
    let mut req = test::TestRequest::post()
        .uri("/api/v1/files/upload")
        .insert_header(("content-type", multipart_content_type()));
    if let Some(user) = user {
        req = req.insert_header(("X-User-ID", user.to_string()));
    }
    req.set_payload(body)
}

// ── Upload: positive ───────────────────────────────────────────────────

#[actix_web::test]
async fn upload_stores_blob_metadata_and_first_version() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let owner = Uuid::new_v4();

    let body = multipart_body(vec![Part::file(
        "notes.txt",
        "text/plain",
        b"hello otter".to_vec(),
    )]);
    let resp = test::call_service(&app, upload_request(body, Some(owner)).to_request()).await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["file"]["name"], "notes.txt");
    assert_eq!(json["file"]["mime_type"], "text/plain");
    assert_eq!(json["file"]["size_bytes"], 11);
    assert_eq!(json["file"]["owner_id"], owner.to_string());
    assert_eq!(json["file"]["version"], 1);
    assert_eq!(json["file"]["is_trashed"], false);
    assert!(json["file"]["folder_id"].is_null());

    let s3_key = json["file"]["s3_key"].as_str().unwrap().to_string();
    assert!(s3_key.starts_with(&format!("files/{owner}/")));

    // one blob write, and two metadata writes (the file row plus version 1)
    assert_eq!(env.aws.s3_calls("PUT").len(), 1);
    assert_eq!(env.aws.dynamo_calls("PutItem").len(), 2);
    assert_eq!(
        env.aws.s3_calls("PUT")[0].path_only(),
        format!("/test-bucket/{s3_key}")
    );
}

#[actix_web::test]
async fn upload_accepts_owner_id_multipart_field_when_header_absent() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let owner = Uuid::new_v4();

    let body = multipart_body(vec![
        Part::file("a.txt", "text/plain", b"abc".to_vec()),
        Part::field("owner_id", owner.to_string()),
    ]);
    let resp = test::call_service(&app, upload_request(body, None).to_request()).await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["file"]["owner_id"], owner.to_string());
}

#[actix_web::test]
async fn upload_header_owner_wins_over_spoofed_owner_id_field() {
    // authz: a caller must not be able to attribute an upload to somebody else by
    // sending an `owner_id` part — the gateway-injected header takes precedence.
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let authenticated = Uuid::new_v4();
    let victim = Uuid::new_v4();

    let body = multipart_body(vec![
        Part::file("a.txt", "text/plain", b"abc".to_vec()),
        Part::field("owner_id", victim.to_string()),
    ]);
    let resp =
        test::call_service(&app, upload_request(body, Some(authenticated)).to_request()).await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["file"]["owner_id"], authenticated.to_string());
    assert_ne!(json["file"]["owner_id"], victim.to_string());
}

#[actix_web::test]
async fn upload_accepts_folder_id_and_echoes_it() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let owner = Uuid::new_v4();
    let folder = Uuid::new_v4();

    let body = multipart_body(vec![
        Part::file("a.txt", "text/plain", b"abc".to_vec()),
        Part::field("folder_id", folder.to_string()),
    ]);
    let resp = test::call_service(&app, upload_request(body, Some(owner)).to_request()).await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["file"]["folder_id"], folder.to_string());
}

#[actix_web::test]
async fn upload_treats_blank_folder_id_as_root() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let owner = Uuid::new_v4();

    let body = multipart_body(vec![
        Part::file("a.txt", "text/plain", b"abc".to_vec()),
        Part::field("folder_id", "   "),
    ]);
    let resp = test::call_service(&app, upload_request(body, Some(owner)).to_request()).await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert!(json["file"]["folder_id"].is_null());
}

// ── Upload: MAX_UPLOAD_BYTES boundary trio ─────────────────────────────
//
// `handlers.rs:87` compares `file_bytes.len() > max_upload_bytes`, i.e. the limit itself
// is *inclusive*. The trio below pins that: MAX-1 and MAX are accepted, MAX+1 is 413.
// The ceiling is injected (1 KiB) so the assertions do not depend on the 100 MiB default;
// `upload_at_production_limit_is_rejected_one_byte_over` covers the real default.

#[actix_web::test]
async fn upload_one_byte_under_limit_is_accepted() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file(
        "under.bin",
        "application/octet-stream",
        payload_of((TEST_MAX_UPLOAD_BYTES - 1) as usize),
    )]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["file"]["size_bytes"], TEST_MAX_UPLOAD_BYTES - 1);
}

#[actix_web::test]
async fn upload_exactly_at_limit_is_accepted() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file(
        "exact.bin",
        "application/octet-stream",
        payload_of(TEST_MAX_UPLOAD_BYTES as usize),
    )]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["file"]["size_bytes"], TEST_MAX_UPLOAD_BYTES);
}

#[actix_web::test]
async fn upload_one_byte_over_limit_is_rejected_with_413() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file(
        "over.bin",
        "application/octet-stream",
        payload_of((TEST_MAX_UPLOAD_BYTES + 1) as usize),
    )]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 413);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "file_too_large");
    assert!(json["message"]
        .as_str()
        .unwrap()
        .contains(&TEST_MAX_UPLOAD_BYTES.to_string()));
}

#[actix_web::test]
async fn upload_over_limit_writes_nothing_to_s3_or_dynamo() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file(
        "over.bin",
        "application/octet-stream",
        payload_of((TEST_MAX_UPLOAD_BYTES + 1) as usize),
    )]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 413);
    assert!(env.aws.requests().is_empty());
}

/// The 100 MiB production default, exercised end to end. Ignored by default because the
/// body alone is 100 MiB of RAM; run with `cargo test -- --ignored`.
#[actix_web::test]
#[ignore = "allocates 100 MiB; the limit logic itself is covered by the 1 KiB trio"]
async fn upload_at_production_limit_is_rejected_one_byte_over() {
    let env = TestEnv::builder()
        .max_upload_bytes(PROD_MAX_UPLOAD_BYTES)
        .build()
        .await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file(
        "huge.bin",
        "application/octet-stream",
        payload_of((PROD_MAX_UPLOAD_BYTES + 1) as usize),
    )]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 413);
}

// ── Upload: negative / malformed input ─────────────────────────────────

#[actix_web::test]
async fn upload_zero_byte_file_is_rejected_as_missing_field() {
    // Pinning today's behaviour: an empty part is indistinguishable from "no part at all",
    // so a legitimate 0-byte file cannot be uploaded. See FINDING-1 in the PR description.
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file("empty.txt", "text/plain", Vec::new())]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "bad_request");
    assert_eq!(json["message"], "Bad request: file field is required");
}

#[actix_web::test]
#[ignore = "FINDING-1: a genuine 0-byte upload should succeed, not 400"]
async fn upload_zero_byte_file_should_be_accepted() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file("empty.txt", "text/plain", Vec::new())]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
}

#[actix_web::test]
async fn upload_without_file_part_is_rejected() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::field("owner_id", Uuid::new_v4().to_string())]);

    let resp = test::call_service(&app, upload_request(body, None).to_request()).await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["message"], "Bad request: file field is required");
}

#[actix_web::test]
async fn upload_without_any_owner_is_rejected() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file("a.txt", "text/plain", b"abc".to_vec())]);

    let resp = test::call_service(&app, upload_request(body, None).to_request()).await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["message"], "Bad request: owner_id is required");
}

#[actix_web::test]
async fn upload_with_malformed_multipart_body_is_rejected() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        upload_request(
            b"this is not a multipart body".to_vec(),
            Some(Uuid::new_v4()),
        )
        .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    assert!(env.aws.requests().is_empty());
}

#[actix_web::test]
async fn upload_without_multipart_boundary_is_rejected() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file("a.txt", "text/plain", b"abc".to_vec())]);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri("/api/v1/files/upload")
            .insert_header(("content-type", "multipart/form-data"))
            .insert_header(("X-User-ID", Uuid::new_v4().to_string()))
            .set_payload(body)
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    assert!(env.aws.requests().is_empty());
}

#[actix_web::test]
async fn upload_with_non_uuid_owner_id_field_is_rejected() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![
        Part::file("a.txt", "text/plain", b"abc".to_vec()),
        Part::field("owner_id", "not-a-uuid"),
    ]);

    let resp = test::call_service(&app, upload_request(body, None).to_request()).await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert!(json["message"]
        .as_str()
        .unwrap()
        .contains("invalid owner_id"));
}

#[actix_web::test]
async fn upload_with_non_uuid_folder_id_field_is_rejected() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![
        Part::file("a.txt", "text/plain", b"abc".to_vec()),
        Part::field("folder_id", "42"),
    ]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert!(json["message"]
        .as_str()
        .unwrap()
        .contains("invalid folder_id"));
}

#[actix_web::test]
async fn upload_ignores_unknown_multipart_fields() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![
        Part::field("totally_unknown", "ignored"),
        Part::file("a.txt", "text/plain", b"abc".to_vec()),
    ]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
}

// ── Upload: filename and content-type edge cases ───────────────────────

#[actix_web::test]
async fn upload_preserves_unicode_filename() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file(
        "résumé-🦦-最終版.txt",
        "text/plain",
        b"abc".to_vec(),
    )]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["file"]["name"], "résumé-🦦-最終版.txt");
}

#[actix_web::test]
async fn upload_with_path_traversal_filename_cannot_escape_the_owner_prefix() {
    // The traversal attempt survives in the display name, but the S3 key is derived from
    // the owner id and a fresh UUID, so it cannot escape `files/<owner>/`.
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let owner = Uuid::new_v4();
    let body = multipart_body(vec![Part::file(
        "../../../etc/passwd",
        "text/plain",
        b"root:x:0:0".to_vec(),
    )]);

    let resp = test::call_service(&app, upload_request(body, Some(owner)).to_request()).await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    let s3_key = json["file"]["s3_key"].as_str().unwrap();
    assert!(s3_key.starts_with(&format!("files/{owner}/")));
    assert!(!s3_key.contains(".."));
    assert!(!s3_key.contains("etc/passwd"));
    // documented, unsanitised: the name is stored verbatim (FINDING-2)
    assert_eq!(json["file"]["name"], "../../../etc/passwd");
    assert_eq!(
        env.aws.s3_calls("PUT")[0].path_only(),
        format!("/test-bucket/{s3_key}")
    );
}

#[actix_web::test]
async fn upload_trusts_the_declared_content_type_over_the_actual_bytes() {
    // Pins today's behaviour: no sniffing, no validation — the declared part type is what
    // gets persisted and handed to S3 (FINDING-3).
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file(
        "not-really.png",
        "image/png",
        b"plain text, definitely not a PNG".to_vec(),
    )]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["file"]["mime_type"], "image/png");
    assert_eq!(
        env.aws.s3_calls("PUT")[0]
            .headers
            .get("content-type")
            .map(String::as_str),
        Some("image/png")
    );
}

#[actix_web::test]
async fn upload_defaults_content_type_when_the_part_declares_none() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part {
        name: "file",
        filename: Some("blob".into()),
        content_type: None,
        data: b"abc".to_vec(),
    }]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    // `upload_file` falls back to its own default when the part declares no type
    assert_eq!(json["file"]["mime_type"], "application/octet-stream");
}

// ── Upload: backend failures ───────────────────────────────────────────

#[actix_web::test]
async fn upload_maps_s3_failure_to_500_storage_error() {
    let env = TestEnv::builder()
        .responder(|inner| {
            override_when(
                inner,
                |req| req.target.is_none() && req.method == "PUT",
                || s3_error(500, "InternalError"),
            )
        })
        .build()
        .await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file("a.txt", "text/plain", b"abc".to_vec())]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 500);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "storage_error");
    // metadata must not be written when the blob write failed
    assert_eq!(env.aws.dynamo_calls("PutItem").len(), 0);
}

#[actix_web::test]
async fn upload_maps_dynamo_failure_to_500_metadata_error() {
    let env = TestEnv::builder()
        .responder(|inner| {
            override_when(
                inner,
                |req| req.is_dynamo("PutItem"),
                || common::dynamo_error(400, "ResourceNotFoundException"),
            )
        })
        .build()
        .await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file("a.txt", "text/plain", b"abc".to_vec())]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 500);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "metadata_error");
    // the blob is already in S3: an orphaned object is left behind (FINDING-4)
    assert_eq!(env.aws.s3_calls("PUT").len(), 1);
}

#[actix_web::test]
async fn upload_with_chaos_flag_set_targets_the_missing_bucket_and_500s() {
    // Exercises the chaos branch in `upload_file`: with the Redis flag present the write is
    // redirected to `otterworks-files-chaos-nonexistent`, which S3 answers with NoSuchBucket.
    let env = TestEnv::builder()
        .chaos_flag_set(true)
        .responder(|inner| {
            override_when(
                inner,
                |req| {
                    req.target.is_none()
                        && req.path.starts_with("/otterworks-files-chaos-nonexistent/")
                },
                || s3_error(404, "NoSuchBucket"),
            )
        })
        .build()
        .await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file("a.txt", "text/plain", b"abc".to_vec())]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 500);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "storage_error");
    assert!(env.aws.s3_calls("PUT")[0]
        .path
        .starts_with("/otterworks-files-chaos-nonexistent/"));
}

#[actix_web::test]
async fn upload_without_chaos_flag_uses_the_configured_bucket() {
    let env = TestEnv::builder().chaos_flag_set(false).build().await;
    let app = init_app!(env);
    let body = multipart_body(vec![Part::file("a.txt", "text/plain", b"abc".to_vec())]);

    let resp = test::call_service(
        &app,
        upload_request(body, Some(Uuid::new_v4())).to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
    assert!(env.aws.s3_calls("PUT")[0].path.starts_with("/test-bucket/"));
}

// ── Upload: idempotency / concurrency ──────────────────────────────────

#[actix_web::test]
async fn uploading_the_same_file_twice_creates_two_independent_files() {
    // Pins today's behaviour: uploads carry no idempotency key, so a client retry
    // duplicates both the blob and the metadata row (FINDING-5).
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let owner = Uuid::new_v4();

    let make_body = || multipart_body(vec![Part::file("same.txt", "text/plain", b"abc".to_vec())]);

    let first =
        test::call_service(&app, upload_request(make_body(), Some(owner)).to_request()).await;
    assert_eq!(first.status(), 201);
    let first: Value = test::read_body_json(first).await;

    let second =
        test::call_service(&app, upload_request(make_body(), Some(owner)).to_request()).await;
    assert_eq!(second.status(), 201);
    let second: Value = test::read_body_json(second).await;

    assert_ne!(first["file"]["id"], second["file"]["id"]);
    assert_ne!(first["file"]["s3_key"], second["file"]["s3_key"]);
    assert_eq!(env.aws.s3_calls("PUT").len(), 2);
    assert_eq!(env.aws.dynamo_calls("PutItem").len(), 4);
}

#[actix_web::test]
async fn concurrent_uploads_of_the_same_name_both_succeed_with_distinct_keys() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let owner = Uuid::new_v4();

    let make_body = || multipart_body(vec![Part::file("race.txt", "text/plain", b"abc".to_vec())]);

    let (left, right) = futures_util::future::join(
        test::call_service(&app, upload_request(make_body(), Some(owner)).to_request()),
        test::call_service(&app, upload_request(make_body(), Some(owner)).to_request()),
    )
    .await;

    assert_eq!(left.status(), 201);
    assert_eq!(right.status(), 201);
    let left: Value = test::read_body_json(left).await;
    let right: Value = test::read_body_json(right).await;

    assert_eq!(left["file"]["name"], right["file"]["name"]);
    assert_ne!(left["file"]["id"], right["file"]["id"]);
    assert_ne!(left["file"]["s3_key"], right["file"]["s3_key"]);
    assert_eq!(env.aws.s3_calls("PUT").len(), 2);
}

// ── Download ───────────────────────────────────────────────────────────

#[actix_web::test]
async fn download_returns_a_presigned_url_without_touching_the_blob() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "notes.txt", 11, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/download"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["expires_in_secs"], 3600);
    let url = json["url"].as_str().unwrap();
    assert!(url.contains(&format!("files/{owner}/{file_id}")));
    assert!(url.contains("X-Amz-Signature="));
    assert!(url.contains("X-Amz-Expires=3600"));
    // presigning is local: no GET is ever issued against the blob store
    assert!(env.aws.s3_calls("GET").is_empty());
}

#[actix_web::test]
async fn download_of_an_unknown_file_is_404() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{}/download", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "file_not_found");
}

#[actix_web::test]
async fn download_with_a_malformed_file_id_is_400() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/not-a-uuid/download")
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "bad_request");
    assert!(env.aws.requests().is_empty());
}

#[actix_web::test]
async fn download_maps_dynamo_failure_to_500_metadata_error() {
    let env = TestEnv::builder()
        .responder(|inner| {
            override_when(
                inner,
                |req| req.is_dynamo("GetItem"),
                || common::dynamo_error(500, "InternalServerError"),
            )
        })
        .build()
        .await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{}/download", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 500);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "metadata_error");
}

#[actix_web::test]
async fn download_of_a_trashed_file_currently_still_returns_a_url() {
    // Pins today's behaviour. `download_file` never inspects `is_trashed`, so a file the
    // user has "deleted" is still downloadable (FINDING-6).
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "deleted.txt", 5, true));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/download"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
}

#[actix_web::test]
#[ignore = "FINDING-6: a trashed file should not be downloadable"]
async fn download_of_a_trashed_file_should_be_rejected() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "deleted.txt", 5, true));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/download"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
}

#[actix_web::test]
async fn download_of_another_users_file_currently_succeeds() {
    // Pins today's behaviour: `download_file` ignores `X-User-ID` entirely, so any
    // authenticated caller who knows a file id gets a presigned URL for it (FINDING-7).
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let attacker = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "private.txt", 9, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/download"))
            .insert_header(("X-User-ID", attacker.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert!(json["url"]
        .as_str()
        .unwrap()
        .contains(&format!("files/{owner}/{file_id}")));
}

#[actix_web::test]
#[ignore = "FINDING-7: cross-user download must be denied (403/404), not served"]
async fn download_of_another_users_file_should_be_denied() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let attacker = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "private.txt", 9, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/download"))
            .insert_header(("X-User-ID", attacker.to_string()))
            .to_request(),
    )
    .await;

    assert!(
        resp.status() == 403 || resp.status() == 404,
        "expected the request to be denied, got {}",
        resp.status()
    );
}

#[actix_web::test]
async fn download_without_any_user_context_currently_succeeds() {
    // No `X-User-ID` at all: still served (same root cause as FINDING-7).
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "private.txt", 9, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/download"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
}

// ── Versions ───────────────────────────────────────────────────────────

#[actix_web::test]
async fn versions_of_a_file_with_no_versions_is_an_empty_list() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "notes.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/versions"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["versions"].as_array().unwrap().len(), 0);
}

#[actix_web::test]
async fn versions_of_a_file_with_three_versions_returns_all_of_them() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "notes.txt", 3, false));
    for version in 1..=3u32 {
        env.seed_version(version_item(file_id, owner, version, 100 * version as u64));
    }
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/versions"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    let versions = json["versions"].as_array().unwrap();
    assert_eq!(versions.len(), 3);
    assert_eq!(versions[0]["file_id"], file_id.to_string());
    assert_eq!(versions[0]["version"], 1);
    assert_eq!(versions[2]["version"], 3);
    assert_eq!(versions[2]["size_bytes"], 300);
    assert_eq!(versions[0]["created_by"], owner.to_string());
}

#[actix_web::test]
async fn versions_with_a_malformed_file_id_is_400() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/12345/versions")
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    assert!(env.aws.requests().is_empty());
}

#[actix_web::test]
async fn versions_maps_dynamo_failure_to_500_metadata_error() {
    let env = TestEnv::builder()
        .responder(|inner| {
            override_when(
                inner,
                |req| req.is_dynamo("Query"),
                || common::dynamo_error(400, "ResourceNotFoundException"),
            )
        })
        .build()
        .await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{}/versions", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 500);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "metadata_error");
}

#[actix_web::test]
async fn versions_of_an_unknown_file_currently_returns_200_not_404() {
    // `list_versions` never checks that the file exists (FINDING-8): an unknown id is
    // indistinguishable from a file with no versions.
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{}/versions", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
}

#[actix_web::test]
async fn versions_of_another_users_file_currently_succeeds() {
    // Same authz gap as download (FINDING-7): version history leaks across users.
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let attacker = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "private.txt", 9, false));
    env.seed_version(version_item(file_id, owner, 1, 9));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/versions"))
            .insert_header(("X-User-ID", attacker.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["versions"].as_array().unwrap().len(), 1);
}

#[actix_web::test]
#[ignore = "FINDING-7: version history of another user's file must not be readable"]
async fn versions_of_another_users_file_should_be_denied() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let attacker = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "private.txt", 9, false));
    env.seed_version(version_item(file_id, owner, 1, 9));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/versions"))
            .insert_header(("X-User-ID", attacker.to_string()))
            .to_request(),
    )
    .await;

    assert!(resp.status() == 403 || resp.status() == 404);
}

// ── Upload → versions round trip ───────────────────────────────────────

#[actix_web::test]
async fn upload_then_list_versions_shows_exactly_one_version() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let owner = Uuid::new_v4();

    let body = multipart_body(vec![Part::file("a.txt", "text/plain", b"abcd".to_vec())]);
    let created = test::call_service(&app, upload_request(body, Some(owner)).to_request()).await;
    assert_eq!(created.status(), 201);
    let created: Value = test::read_body_json(created).await;
    let file_id = created["file"]["id"].as_str().unwrap().to_string();

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/versions"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    let versions = json["versions"].as_array().unwrap();
    assert_eq!(versions.len(), 1);
    assert_eq!(versions[0]["version"], 1);
    assert_eq!(versions[0]["size_bytes"], 4);
    assert_eq!(versions[0]["created_by"], owner.to_string());
}

#[actix_web::test]
async fn upload_then_download_round_trips_through_the_metadata_store() {
    let env = TestEnv::new().await;
    let app = init_app!(env);
    let owner = Uuid::new_v4();

    let body = multipart_body(vec![Part::file("a.txt", "text/plain", b"abcd".to_vec())]);
    let created = test::call_service(&app, upload_request(body, Some(owner)).to_request()).await;
    let created: Value = test::read_body_json(created).await;
    let file_id = created["file"]["id"].as_str().unwrap().to_string();
    let s3_key = created["file"]["s3_key"].as_str().unwrap().to_string();

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}/download"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert!(json["url"].as_str().unwrap().contains(&s3_key));
}

// ── Harness sanity ─────────────────────────────────────────────────────

#[actix_web::test]
async fn mock_state_starts_empty_for_every_test() {
    // Guards the "no shared mutable fixtures" rule: each TestEnv owns its own state.
    let first = TestEnv::new().await;
    let second = TestEnv::new().await;
    first.seed_file(file_item(Uuid::new_v4(), Uuid::new_v4(), "a", 1, false));

    let first_len = first.state.lock().unwrap().files.len();
    let second_len = second.state.lock().unwrap().files.len();
    assert_eq!(first_len, 1);
    assert_eq!(second_len, 0);
    assert_ne!(first.aws.endpoint(), second.aws.endpoint());
    let _ = MockState::default();
}

// ── File metadata detail ───────────────────────────────────────────────

#[actix_web::test]
async fn get_file_metadata_returns_the_file_and_its_shares() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    let peer = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "notes.txt", 12, false));
    env.seed_share(share_item(Uuid::new_v4(), file_id, peer, owner, "viewer"));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{file_id}"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["id"], file_id.to_string());
    assert_eq!(json["name"], "notes.txt");
    let shares = json["shared_with"].as_array().unwrap();
    assert_eq!(shares.len(), 1);
    assert_eq!(shares[0]["shared_with"], peer.to_string());
    assert_eq!(shares[0]["permission"], "viewer");
}

#[actix_web::test]
async fn get_file_metadata_of_an_unknown_file_is_404() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files/{}", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "file_not_found");
}

#[actix_web::test]
async fn get_file_metadata_with_a_malformed_id_is_400() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/nope")
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
}

// ── list_files: filtering and pagination boundaries ────────────────────

fn seed_files(env: &TestEnv, owner: Uuid, count: usize) -> Vec<Uuid> {
    (0..count)
        .map(|i| {
            let id = Uuid::new_v4();
            env.seed_file(file_item(id, owner, &format!("file-{i}.txt"), 1, false));
            id
        })
        .collect()
}

#[actix_web::test]
async fn list_files_with_no_files_returns_an_empty_page() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files")
            .insert_header(("X-User-ID", Uuid::new_v4().to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["total"], 0);
    assert_eq!(json["page"], 1);
    assert_eq!(json["page_size"], 50);
    assert_eq!(json["files"].as_array().unwrap().len(), 0);
}

#[actix_web::test]
async fn list_files_returns_the_owners_files_with_default_paging() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    seed_files(&env, owner, 3);
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files")
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["total"], 3);
    assert_eq!(json["files"].as_array().unwrap().len(), 3);
}

#[actix_web::test]
async fn list_files_paginates_and_returns_an_empty_page_past_the_end() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    seed_files(&env, owner, 5);
    let app = init_app!(env);

    let page_len = |page: u32| {
        let app = &app;
        async move {
            let resp = test::call_service(
                app,
                test::TestRequest::get()
                    .uri(&format!("/api/v1/files?page={page}&page_size=2"))
                    .insert_header(("X-User-ID", owner.to_string()))
                    .to_request(),
            )
            .await;
            assert_eq!(resp.status(), 200);
            let json: Value = test::read_body_json(resp).await;
            assert_eq!(json["total"], 5);
            json["files"].as_array().unwrap().len()
        }
    };

    assert_eq!(page_len(1).await, 2);
    assert_eq!(page_len(2).await, 2);
    assert_eq!(page_len(3).await, 1);
    assert_eq!(page_len(4).await, 0);
}

#[actix_web::test]
async fn list_files_clamps_page_zero_up_to_the_first_page() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    seed_files(&env, owner, 2);
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files?page=0")
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["page"], 1);
    assert_eq!(json["files"].as_array().unwrap().len(), 2);
}

// page_size is clamped with `.min(100)`; boundary trio around that ceiling.

#[actix_web::test]
async fn list_files_page_size_one_under_the_ceiling_is_honoured() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files?page_size=99")
            .insert_header(("X-User-ID", Uuid::new_v4().to_string()))
            .to_request(),
    )
    .await;

    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["page_size"], 99);
}

#[actix_web::test]
async fn list_files_page_size_at_the_ceiling_is_honoured() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files?page_size=100")
            .insert_header(("X-User-ID", Uuid::new_v4().to_string()))
            .to_request(),
    )
    .await;

    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["page_size"], 100);
}

#[actix_web::test]
async fn list_files_page_size_over_the_ceiling_is_clamped() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files?page_size=101")
            .insert_header(("X-User-ID", Uuid::new_v4().to_string()))
            .to_request(),
    )
    .await;

    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["page_size"], 100);
}

#[actix_web::test]
async fn list_files_rejects_a_malformed_owner_id_query() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files?owner_id=not-a-uuid")
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    assert!(env.aws.requests().is_empty());
}

#[actix_web::test]
async fn list_files_prefers_the_header_owner_over_a_spoofed_query_owner() {
    // authz: `resolve_owner_id` must not let a caller scan another user's files by
    // passing `?owner_id=`. The scan filter is built from the header value.
    let env = TestEnv::new().await;
    let authenticated = Uuid::new_v4();
    let victim = Uuid::new_v4();
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/files?owner_id={victim}"))
            .insert_header(("X-User-ID", authenticated.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let scan = &env.aws.dynamo_calls("Scan")[0].json();
    let values = scan["ExpressionAttributeValues"].to_string();
    assert!(values.contains(&authenticated.to_string()));
    assert!(!values.contains(&victim.to_string()));
}

#[actix_web::test]
async fn list_trashed_returns_the_trash_page() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    env.seed_file(file_item(Uuid::new_v4(), owner, "gone.txt", 3, true));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/trash")
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["total"], 1);
    assert_eq!(json["files"][0]["is_trashed"], true);
}

// ── Shared-with-me listing ─────────────────────────────────────────────

#[actix_web::test]
async fn list_shared_files_requires_a_user_header() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/shared")
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["message"], "Bad request: missing X-User-ID header");
}

#[actix_web::test]
async fn list_shared_files_deduplicates_duplicate_share_rows() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let peer = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "shared.txt", 4, false));
    env.seed_share(share_item(Uuid::new_v4(), file_id, peer, owner, "viewer"));
    env.seed_share(share_item(Uuid::new_v4(), file_id, peer, owner, "editor"));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/shared")
            .insert_header(("X-User-ID", peer.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["total"], 1);
    assert_eq!(json["files"][0]["id"], file_id.to_string());
}

#[actix_web::test]
async fn list_shared_files_hides_trashed_files() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let peer = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "shared.txt", 4, true));
    env.seed_share(share_item(Uuid::new_v4(), file_id, peer, owner, "viewer"));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/shared")
            .insert_header(("X-User-ID", peer.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["total"], 0);
}

// ── Delete ─────────────────────────────────────────────────────────────

#[actix_web::test]
async fn delete_file_removes_the_row_and_the_blob() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "bye.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::delete()
            .uri(&format!("/api/v1/files/{file_id}"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 204);
    assert!(env.stored_file(file_id).is_none());
    assert_eq!(
        env.aws.s3_calls("DELETE")[0].path_only(),
        format!("/test-bucket/files/{owner}/{file_id}")
    );
}

#[actix_web::test]
async fn delete_file_of_an_unknown_file_is_404_and_touches_no_blob() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::delete()
            .uri(&format!("/api/v1/files/{}", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
    assert!(env.aws.s3_calls("DELETE").is_empty());
}

#[actix_web::test]
async fn delete_file_reports_an_s3_failure_as_500() {
    let env = TestEnv::builder()
        .responder(|inner| {
            override_when(
                inner,
                |req| req.target.is_none() && req.method == "DELETE",
                || s3_error(403, "AccessDenied"),
            )
        })
        .build()
        .await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "bye.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::delete()
            .uri(&format!("/api/v1/files/{file_id}"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 500);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "storage_error");
    // the metadata row is already gone: the blob is orphaned (FINDING-4)
    assert!(env.stored_file(file_id).is_none());
}

// ── Move / rename ──────────────────────────────────────────────────────

#[actix_web::test]
async fn move_file_sets_the_target_folder() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    let folder = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::put()
            .uri(&format!("/api/v1/files/{file_id}/move"))
            .set_json(serde_json::json!({ "folder_id": folder }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["folder_id"], folder.to_string());
}

#[actix_web::test]
async fn move_file_to_the_root_clears_the_folder() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    let folder = Uuid::new_v4();
    env.seed_file(file_item_in_folder(file_id, owner, "a.txt", folder, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::put()
            .uri(&format!("/api/v1/files/{file_id}/move"))
            .set_json(serde_json::json!({ "folder_id": Value::Null }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert!(json["folder_id"].is_null());
}

#[actix_web::test]
async fn move_file_of_an_unknown_file_is_404() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::put()
            .uri(&format!("/api/v1/files/{}/move", Uuid::new_v4()))
            .set_json(serde_json::json!({ "folder_id": Uuid::new_v4() }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
}

#[actix_web::test]
async fn rename_file_updates_the_stored_name() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "old.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::patch()
            .uri(&format!("/api/v1/files/{file_id}/rename"))
            .set_json(serde_json::json!({ "name": "  new.txt  " }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["name"], "new.txt");
    assert_eq!(env.stored_file(file_id).unwrap()["name"]["S"], "new.txt");
}

#[actix_web::test]
async fn rename_file_rejects_a_blank_name() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "old.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::patch()
            .uri(&format!("/api/v1/files/{file_id}/rename"))
            .set_json(serde_json::json!({ "name": "   " }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["message"], "Bad request: name cannot be empty");
    assert_eq!(env.stored_file(file_id).unwrap()["name"]["S"], "old.txt");
}

#[actix_web::test]
async fn rename_file_of_an_unknown_file_is_404() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::patch()
            .uri(&format!("/api/v1/files/{}/rename", Uuid::new_v4()))
            .set_json(serde_json::json!({ "name": "new.txt" }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
}

#[actix_web::test]
async fn rename_file_of_another_users_file_currently_succeeds() {
    // Same authz gap as download (FINDING-7): mutating routes never compare the caller
    // against `owner_id` either.
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let attacker = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "private.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::patch()
            .uri(&format!("/api/v1/files/{file_id}/rename"))
            .insert_header(("X-User-ID", attacker.to_string()))
            .set_json(serde_json::json!({ "name": "pwned.txt" }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    assert_eq!(env.stored_file(file_id).unwrap()["name"]["S"], "pwned.txt");
}

#[actix_web::test]
#[ignore = "FINDING-7: renaming another user's file must be denied"]
async fn rename_file_of_another_users_file_should_be_denied() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let attacker = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "private.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::patch()
            .uri(&format!("/api/v1/files/{file_id}/rename"))
            .insert_header(("X-User-ID", attacker.to_string()))
            .set_json(serde_json::json!({ "name": "pwned.txt" }))
            .to_request(),
    )
    .await;

    assert!(resp.status() == 403 || resp.status() == 404);
}

// ── Trash / restore ────────────────────────────────────────────────────

#[actix_web::test]
async fn trash_file_marks_the_row_as_trashed() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{file_id}/trash"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["is_trashed"], true);
}

#[actix_web::test]
async fn trash_file_of_an_unknown_file_is_404() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{}/trash", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
}

#[actix_web::test]
async fn trashing_an_already_trashed_file_is_idempotent() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, true));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{file_id}/trash"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["is_trashed"], true);
}

#[actix_web::test]
async fn restore_file_clears_the_trashed_flag() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, true));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{file_id}/restore"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["is_trashed"], false);
    assert_eq!(
        env.stored_file(file_id).unwrap()["is_trashed"]["BOOL"],
        false
    );
}

#[actix_web::test]
async fn restore_file_of_an_unknown_file_is_404() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{}/restore", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
}

// ── Sharing ────────────────────────────────────────────────────────────

fn share_body(shared_with: Uuid, shared_by: Uuid, permission: &str) -> Value {
    serde_json::json!({
        "shared_with": shared_with,
        "permission": permission,
        "shared_by": shared_by,
    })
}

#[actix_web::test]
async fn share_file_creates_a_share() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let peer = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{file_id}/share"))
            .set_json(share_body(peer, owner, "viewer"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["share"]["file_id"], file_id.to_string());
    assert_eq!(json["share"]["shared_with"], peer.to_string());
    assert_eq!(json["share"]["permission"], "viewer");
    assert_eq!(env.stored_share_count(), 1);
}

#[actix_web::test]
async fn sharing_the_same_file_with_the_same_user_twice_is_idempotent() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let peer = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, false));
    let app = init_app!(env);

    let first = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{file_id}/share"))
            .set_json(share_body(peer, owner, "viewer"))
            .to_request(),
    )
    .await;
    assert_eq!(first.status(), 201);
    let first: Value = test::read_body_json(first).await;

    let second = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{file_id}/share"))
            .set_json(share_body(peer, owner, "viewer"))
            .to_request(),
    )
    .await;
    assert_eq!(second.status(), 200);
    let second: Value = test::read_body_json(second).await;

    assert_eq!(first["share"]["id"], second["share"]["id"]);
    assert_eq!(env.stored_share_count(), 1);
}

#[actix_web::test]
async fn resharing_with_a_different_permission_updates_in_place() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let peer = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    let share_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, false));
    env.seed_share(share_item(share_id, file_id, peer, owner, "viewer"));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{file_id}/share"))
            .set_json(share_body(peer, owner, "editor"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["share"]["id"], share_id.to_string());
    assert_eq!(json["share"]["permission"], "editor");
    assert_eq!(env.stored_share_count(), 1);
}

#[actix_web::test]
async fn share_file_of_an_unknown_file_is_404() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{}/share", Uuid::new_v4()))
            .set_json(share_body(Uuid::new_v4(), Uuid::new_v4(), "viewer"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
}

#[actix_web::test]
async fn share_file_rejects_an_unknown_permission() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri(&format!("/api/v1/files/{file_id}/share"))
            .set_json(share_body(Uuid::new_v4(), owner, "owner"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
}

#[actix_web::test]
async fn remove_share_deletes_the_share_row() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let peer = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, false));
    env.seed_share(share_item(Uuid::new_v4(), file_id, peer, owner, "viewer"));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::delete()
            .uri(&format!("/api/v1/files/{file_id}/share/{peer}"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 204);
    assert_eq!(env.stored_share_count(), 0);
}

#[actix_web::test]
async fn remove_share_that_does_not_exist_is_404() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "a.txt", 3, false));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::delete()
            .uri(&format!("/api/v1/files/{file_id}/share/{}", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "share_not_found");
}

#[actix_web::test]
async fn remove_share_with_a_malformed_user_id_is_400() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::delete()
            .uri(&format!("/api/v1/files/{}/share/nope", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert!(json["message"]
        .as_str()
        .unwrap()
        .contains("invalid user id"));
}

// ── Folders ────────────────────────────────────────────────────────────

#[actix_web::test]
async fn create_folder_persists_and_returns_it() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri("/api/v1/folders")
            .set_json(serde_json::json!({ "name": "Reports", "owner_id": owner }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 201);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["name"], "Reports");
    assert_eq!(json["owner_id"], owner.to_string());
    assert!(json["parent_id"].is_null());
    assert_eq!(env.stored_folder_count(), 1);
}

#[actix_web::test]
async fn create_folder_requires_an_owner() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::post()
            .uri("/api/v1/folders")
            .set_json(serde_json::json!({ "name": "Reports" }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    assert_eq!(env.stored_folder_count(), 0);
}

#[actix_web::test]
async fn get_folder_returns_a_seeded_folder() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let folder_id = Uuid::new_v4();
    env.seed_folder(folder_item(folder_id, owner, "Reports", None));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/folders/{folder_id}"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["id"], folder_id.to_string());
    assert_eq!(json["name"], "Reports");
}

#[actix_web::test]
async fn get_folder_of_an_unknown_folder_is_404() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/folders/{}", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["error"], "folder_not_found");
}

#[actix_web::test]
async fn get_folder_with_a_malformed_id_is_400() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/folders/nope")
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    assert!(env.aws.requests().is_empty());
}

#[actix_web::test]
async fn update_folder_renames_it() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let folder_id = Uuid::new_v4();
    env.seed_folder(folder_item(folder_id, owner, "Old", None));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::put()
            .uri(&format!("/api/v1/folders/{folder_id}"))
            .set_json(serde_json::json!({ "name": "New" }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["name"], "New");
}

#[actix_web::test]
async fn update_folder_reparents_it() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let folder_id = Uuid::new_v4();
    let parent_id = Uuid::new_v4();
    env.seed_folder(folder_item(folder_id, owner, "Child", None));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::put()
            .uri(&format!("/api/v1/folders/{folder_id}"))
            .set_json(serde_json::json!({ "parent_id": parent_id }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["parent_id"], parent_id.to_string());
}

#[actix_web::test]
async fn update_folder_of_an_unknown_folder_is_404() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::put()
            .uri(&format!("/api/v1/folders/{}", Uuid::new_v4()))
            .set_json(serde_json::json!({ "name": "New" }))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 404);
}

#[actix_web::test]
async fn delete_folder_removes_the_row() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let folder_id = Uuid::new_v4();
    env.seed_folder(folder_item(folder_id, owner, "Reports", None));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::delete()
            .uri(&format!("/api/v1/folders/{folder_id}"))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 204);
    assert_eq!(env.stored_folder_count(), 0);
}

#[actix_web::test]
async fn delete_folder_of_an_unknown_folder_is_still_204() {
    // Pins today's behaviour: `delete_folder` does not read the row first, so deleting a
    // folder that never existed is reported as success (FINDING-10).
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::delete()
            .uri(&format!("/api/v1/folders/{}", Uuid::new_v4()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 204);
}

#[actix_web::test]
async fn list_folders_returns_the_owners_root_folders() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    env.seed_folder(folder_item(Uuid::new_v4(), owner, "A", None));
    env.seed_folder(folder_item(Uuid::new_v4(), owner, "B", None));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/folders")
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["folders"].as_array().unwrap().len(), 2);
}

#[actix_web::test]
async fn list_folders_scans_by_parent_when_one_is_given() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let parent = Uuid::new_v4();
    env.seed_folder(folder_item(Uuid::new_v4(), owner, "Child", Some(parent)));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri(&format!("/api/v1/folders?parent_id={parent}"))
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let scan = env.aws.dynamo_calls("Scan")[0].json();
    assert!(scan["FilterExpression"]
        .as_str()
        .unwrap()
        .contains("parent_id = :parent_id"));
}

// ── Activity feed ──────────────────────────────────────────────────────

#[actix_web::test]
async fn activity_requires_a_user_header() {
    let env = TestEnv::new().await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/activity")
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 400);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["message"], "Bad request: missing owner context");
}

#[actix_web::test]
async fn activity_merges_uploads_and_shares_newest_first() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    let peer = Uuid::new_v4();
    let file_id = Uuid::new_v4();
    env.seed_file(file_item(file_id, owner, "notes.txt", 3, false));
    env.seed_share(share_item(Uuid::new_v4(), file_id, peer, owner, "viewer"));
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/activity")
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    let items = json["items"].as_array().unwrap();
    assert_eq!(items.len(), 2);
    // the share row is dated a day after the file row
    assert_eq!(items[0]["type"], "share");
    assert_eq!(items[0]["description"], "Shared notes.txt");
    assert_eq!(items[1]["type"], "upload");
    assert_eq!(items[1]["description"], "Uploaded notes.txt");
    assert_eq!(items[1]["resource_id"], file_id.to_string());
}

// The activity feed caps `limit` with `.min(50)`; boundary trio around that cap.

fn seed_activity(env: &TestEnv, owner: Uuid, count: usize) {
    for i in 0..count {
        env.seed_file(file_item(
            Uuid::new_v4(),
            owner,
            &format!("file-{i}.txt"),
            1,
            false,
        ));
    }
}

#[actix_web::test]
async fn activity_limit_one_under_the_cap_is_honoured() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    seed_activity(&env, owner, 60);
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/activity?limit=49")
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["items"].as_array().unwrap().len(), 49);
}

#[actix_web::test]
async fn activity_limit_at_the_cap_is_honoured() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    seed_activity(&env, owner, 60);
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/activity?limit=50")
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["items"].as_array().unwrap().len(), 50);
}

#[actix_web::test]
async fn activity_limit_over_the_cap_is_clamped() {
    let env = TestEnv::new().await;
    let owner = Uuid::new_v4();
    seed_activity(&env, owner, 60);
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/activity?limit=51")
            .insert_header(("X-User-ID", owner.to_string()))
            .to_request(),
    )
    .await;

    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["items"].as_array().unwrap().len(), 50);
}

#[actix_web::test]
async fn activity_falls_back_to_an_empty_feed_when_dynamo_fails() {
    // `list_activity` swallows metadata errors (`unwrap_or_default`), so a broken table
    // renders as "no activity" rather than a 500 (FINDING-11).
    let env = TestEnv::builder()
        .responder(|inner| {
            override_when(
                inner,
                |req| req.is_dynamo("Scan"),
                || common::dynamo_error(400, "ResourceNotFoundException"),
            )
        })
        .build()
        .await;
    let app = init_app!(env);

    let resp = test::call_service(
        &app,
        test::TestRequest::get()
            .uri("/api/v1/files/activity")
            .insert_header(("X-User-ID", Uuid::new_v4().to_string()))
            .to_request(),
    )
    .await;

    assert_eq!(resp.status(), 200);
    let json: Value = test::read_body_json(resp).await;
    assert_eq!(json["items"].as_array().unwrap().len(), 0);
}

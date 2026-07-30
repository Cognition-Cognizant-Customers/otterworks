#![cfg(feature = "integration")]

#[path = "support/mod.rs"]
mod support;

use support::{setup, unique_key};

#[tokio::test]
async fn s3_upload_download_round_trip_preserves_bytes() {
    let context = setup().await;
    let key = unique_key("integration-s3/round-trip");
    let content = b"LocalStack integration content";

    context
        .s3
        .upload_object(&key, support::bytes(content), "text/plain")
        .await
        .expect("upload object");

    let downloaded = context
        .s3
        .download_object(&key)
        .await
        .expect("download object");
    assert_eq!(downloaded.as_ref(), content);

    context.s3.delete_object(&key).await.expect("delete object");
}

#[tokio::test]
async fn s3_copy_duplicates_an_object() {
    let context = setup().await;
    let source_key = unique_key("integration-s3/copy-source");
    let copy_key = unique_key("integration-s3/copy-destination");
    let content = b"copy me";

    context
        .s3
        .upload_object(&source_key, support::bytes(content), "text/plain")
        .await
        .expect("upload object");
    context
        .s3
        .copy_object(&source_key, &copy_key)
        .await
        .expect("copy object");

    let copied = context
        .s3
        .download_object(&copy_key)
        .await
        .expect("download copied object");
    assert_eq!(copied.as_ref(), content);

    context
        .s3
        .delete_object(&source_key)
        .await
        .expect("delete source");
    context
        .s3
        .delete_object(&copy_key)
        .await
        .expect("delete copy");
}

#[tokio::test]
async fn s3_presigned_download_url_contains_object_key() {
    let context = setup().await;
    let key = unique_key("integration-s3/presigned");

    context
        .s3
        .upload_object(&key, support::bytes(b"presign me"), "text/plain")
        .await
        .expect("upload object");

    let url = context
        .s3
        .presigned_download_url(&key, 60)
        .await
        .expect("presigned URL");
    assert!(url.contains(&key));

    context.s3.delete_object(&key).await.expect("delete object");
}

#[tokio::test]
async fn s3_delete_removes_object() {
    let context = setup().await;
    let key = unique_key("integration-s3/delete");

    context
        .s3
        .upload_object(&key, support::bytes(b"delete me"), "text/plain")
        .await
        .expect("upload object");
    context.s3.delete_object(&key).await.expect("delete object");

    assert!(context.s3.download_object(&key).await.is_err());
}

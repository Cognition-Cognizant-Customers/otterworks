#![allow(dead_code)]

use actix_web::{middleware as actix_middleware, web, App, HttpServer};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

mod config;
mod errors;
mod events;
mod handlers;
mod metadata;
mod middleware;
mod models;
mod storage;
#[cfg(test)]
mod test_support;

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    dotenvy::dotenv().ok();

    tracing_subscriber::registry()
        .with(tracing_subscriber::EnvFilter::new(
            std::env::var("RUST_LOG")
                .unwrap_or_else(|_| "file_service=debug,actix_web=info".into()),
        ))
        .with(tracing_subscriber::fmt::layer().json())
        .init();

    let app_config = config::AppConfig::from_env();
    let s3_client = storage::S3Client::new(&app_config.aws).await;
    let meta_client = metadata::MetadataClient::new(&app_config.aws).await;
    let event_publisher = events::EventPublisher::new(&app_config.sns, &app_config.aws).await;

    let redis_url = {
        let host = std::env::var("REDIS_HOST").unwrap_or_else(|_| "localhost".into());
        let port = std::env::var("REDIS_PORT").unwrap_or_else(|_| "6379".into());
        format!("redis://{}:{}", host, port)
    };
    let redis_client = redis::Client::open(redis_url).expect("invalid Redis URL");
    let redis_cm = redis::aio::ConnectionManager::new(redis_client)
        .await
        .expect("failed to connect to Redis");

    let port = app_config.server.port;
    tracing::info!(port = %port, "File Service starting");

    let config_data = web::Data::new(app_config);
    let s3_data = web::Data::new(s3_client);
    let meta_data = web::Data::new(meta_client);
    let events_data = web::Data::new(event_publisher);
    let redis_data = web::Data::new(redis_cm);

    HttpServer::new(move || {
        App::new()
            .wrap(tracing_actix_web::TracingLogger::default())
            .wrap(actix_middleware::Compress::default())
            .wrap(middleware::RequestId)
            .app_data(config_data.clone())
            .app_data(s3_data.clone())
            .app_data(meta_data.clone())
            .app_data(events_data.clone())
            .app_data(redis_data.clone())
            .configure(handlers::configure_routes)
    })
    .bind(format!("0.0.0.0:{port}"))?
    .run()
    .await
}

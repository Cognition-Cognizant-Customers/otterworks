pub mod config;
pub mod errors;
pub mod events;
pub mod handlers;
pub mod metadata;
pub mod middleware;
pub mod models;
pub mod storage;

use actix_web::web;

pub fn configure_routes(cfg: &mut web::ServiceConfig) {
    cfg.route("/health", web::get().to(handlers::health))
        .route("/metrics", web::get().to(handlers::metrics))
        .service(
            web::scope("/api/v1/files")
                .route("/upload", web::post().to(handlers::upload_file))
                .route("/shared", web::get().to(handlers::list_shared_files))
                .route("/trash", web::get().to(handlers::list_trashed))
                .route("/activity", web::get().to(handlers::list_activity))
                .route("", web::get().to(handlers::list_files))
                .route("/{file_id}", web::get().to(handlers::get_file_metadata))
                .route("/{file_id}", web::delete().to(handlers::delete_file))
                .route(
                    "/{file_id}/download",
                    web::get().to(handlers::download_file),
                )
                .route("/{file_id}/move", web::put().to(handlers::move_file))
                .route("/{file_id}/rename", web::patch().to(handlers::rename_file))
                .route(
                    "/{file_id}/versions",
                    web::get().to(handlers::list_versions),
                )
                .route("/{file_id}/trash", web::post().to(handlers::trash_file))
                .route("/{file_id}/restore", web::post().to(handlers::restore_file))
                .route("/{file_id}/share", web::post().to(handlers::share_file))
                .route(
                    "/{file_id}/share/{user_id}",
                    web::delete().to(handlers::remove_share),
                ),
        )
        .service(
            web::scope("/api/v1/folders")
                .route("", web::get().to(handlers::list_folders))
                .route("", web::post().to(handlers::create_folder))
                .route("/{folder_id}", web::get().to(handlers::get_folder))
                .route("/{folder_id}", web::put().to(handlers::update_folder))
                .route("/{folder_id}", web::delete().to(handlers::delete_folder)),
        );
}

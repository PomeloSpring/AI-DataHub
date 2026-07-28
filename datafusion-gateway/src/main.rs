mod api;
mod engine;
mod providers;
mod schema;
mod security;
mod store;
mod types;

use std::env;

use axum::Router;
use tokio::net::TcpListener;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing::info;
use tracing_subscriber::{fmt, EnvFilter};

use api::query::AppState;
use providers::ConnectionPoolManager;
use store::DatasourceStore;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Initialize tracing
    let log_level = env::var("RUST_LOG").unwrap_or_else(|_| "info".to_string());
    fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| {
            EnvFilter::new(&log_level)
        }))
        .init();

    // Configuration
    let port = env::var("GATEWAY_PORT")
        .unwrap_or_else(|_| "50051".to_string())
        .parse::<u16>()
        .unwrap_or(50051);

    let db_path = env::var("GATEWAY_DB_PATH")
        .unwrap_or_else(|_| "datafusion.db".to_string());

    // Application state
    let datasource_store = DatasourceStore::new(&db_path)
        .expect("Failed to initialize datasource store");
    let state = AppState {
        pool_manager: ConnectionPoolManager::new(),
        datasource_store,
    };

    // Build router
    let app = Router::new()
        // Query & Health
        .route("/api/query", axum::routing::post(api::query::handle_query))
        .route("/api/health", axum::routing::get(api::health::handle_health))
        // Datasource CRUD
        .route("/api/datasources", axum::routing::get(api::datasources::list_datasources))
        .route("/api/datasources", axum::routing::post(api::datasources::create_datasource))
        .route("/api/datasources/{id}", axum::routing::get(api::datasources::get_datasource))
        .route("/api/datasources/{id}", axum::routing::put(api::datasources::update_datasource))
        .route("/api/datasources/{id}", axum::routing::delete(api::datasources::delete_datasource))
        .route("/api/datasources/{id}/test", axum::routing::post(api::datasources::test_datasource))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    // Start server
    let listener = TcpListener::bind(format!("0.0.0.0:{}", port)).await?;
    info!("DataFusion Gateway v{} listening on port {}", env!("CARGO_PKG_VERSION"), port);

    axum::serve(listener, app).await?;

    Ok(())
}

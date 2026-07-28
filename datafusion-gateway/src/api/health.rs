use axum::Json;
use std::time::Instant;

use crate::types::HealthResponse;

/// Global start time for uptime calculation
static START_TIME: once_cell::sync::Lazy<Instant> = once_cell::sync::Lazy::new(Instant::now);

/// GET /api/health
pub async fn handle_health() -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok".to_string(),
        version: env!("CARGO_PKG_VERSION").to_string(),
        uptime_seconds: START_TIME.elapsed().as_secs(),
    })
}

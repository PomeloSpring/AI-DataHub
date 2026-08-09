/// Datasource CRUD API — manage database connections stored in SQLite.

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::Json;
use serde_json::{json, Value};

use crate::api::query::AppState;
use crate::store::CreateDatasource;

/// GET /api/datasources — list all (without passwords)
pub async fn list_datasources(
    State(state): State<AppState>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let store = state.datasource_store.clone();
    let records = tokio::task::spawn_blocking(move || store.list())
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;

    Ok(Json(json!({ "datasources": records })))
}

/// POST /api/datasources — create a new datasource
pub async fn create_datasource(
    State(state): State<AppState>,
    Json(body): Json<CreateDatasource>,
) -> Result<(StatusCode, Json<Value>), (StatusCode, String)> {
    if body.host.is_empty() || body.database.is_empty() {
        return Err((StatusCode::BAD_REQUEST, "host and database are required".into()));
    }

    let store = state.datasource_store.clone();
    let ds = body.clone();
    let id = tokio::task::spawn_blocking(move || store.create(&ds))
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e))?;

    Ok((StatusCode::CREATED, Json(json!({ "id": id, "success": true }))))
}

/// GET /api/datasources/:id — get one (without password)
pub async fn get_datasource(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let store = state.datasource_store.clone();
    let record = tokio::task::spawn_blocking(move || store.get_record(&id))
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map_err(|e| (StatusCode::NOT_FOUND, e))?;

    Ok(Json(json!(record)))
}

/// PUT /api/datasources/:id — update
pub async fn update_datasource(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<CreateDatasource>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let store = state.datasource_store.clone();
    tokio::task::spawn_blocking(move || store.update(&id, &body))
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map_err(|e| (StatusCode::NOT_FOUND, e))?;

    Ok(Json(json!({ "success": true })))
}

/// DELETE /api/datasources/:id
pub async fn delete_datasource(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let store = state.datasource_store.clone();
    tokio::task::spawn_blocking(move || store.delete(&id))
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map_err(|e| (StatusCode::NOT_FOUND, e))?;

    Ok(Json(json!({ "success": true })))
}

/// POST /api/datasources/:id/test — test connection
pub async fn test_datasource(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let store = state.datasource_store.clone();
    let config = tokio::task::spawn_blocking(move || store.get(&id))
        .await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?
        .map_err(|e| (StatusCode::NOT_FOUND, e))?;

    // Try to connect and run a simple query
    let result = test_mysql_connection(&config).await;

    match result {
        Ok(info) => Ok(Json(json!({
            "success": true,
            "message": format!("Connected to {}:{}/{}", config.host, config.port, config.database),
            "info": info,
        }))),
        Err(e) => Ok(Json(json!({
            "success": false,
            "message": format!("Connection failed: {}", e),
        }))),
    }
}

async fn test_mysql_connection(config: &crate::types::DatasourceConfig) -> Result<Value, String> {
    use mysql_async::prelude::*;
    use mysql_async::{Pool, OptsBuilder};

    let opts = OptsBuilder::default()
        .ip_or_hostname(config.host.clone())
        .tcp_port(config.port)
        .db_name(Some(config.database.clone()))
        .user(Some(config.user.clone()))
        .pass(Some(config.password.clone()));

    let pool = Pool::new(opts);
    let mut conn = pool.get_conn().await.map_err(|e| format!("Connect failed: {}", e))?;

    let version: String = conn
        .query_first("SELECT VERSION()")
        .await
        .map_err(|e| format!("Query failed: {}", e))?
        .unwrap_or_default();

    let table_count: i64 = conn
        .query_first(format!(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = '{}'",
            config.database.replace('\'', "''")
        ))
        .await
        .map_err(|e| format!("Table count failed: {}", e))?
        .unwrap_or(0);

    pool.disconnect().await.ok();

    Ok(json!({
        "version": version,
        "table_count": table_count,
    }))
}

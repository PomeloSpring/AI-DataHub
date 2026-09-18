use axum::extract::State;
use axum::http::StatusCode;
use axum::Json;
use tracing::{info, warn};

use crate::engine::{QueryExecutor, QuerySession, is_metadata_query, is_passthrough_datasource, execute_metadata_query, needs_pushdown, execute_pushdown_query, execute_passthrough_with_rls};
use crate::providers::ConnectionPoolManager;
use crate::store::DatasourceStore;
use crate::types::{DatasourceConfig, QueryRequest, QueryResponse};

/// Application state shared across requests
#[derive(Clone)]
pub struct AppState {
    pub pool_manager: ConnectionPoolManager,
    pub datasource_store: DatasourceStore,
}

/// Resolve datasource config from request: prefer datasource_id, fallback to inline.
fn resolve_datasource(request: &QueryRequest, store: &DatasourceStore) -> Result<DatasourceConfig, String> {
    if let Some(id) = &request.datasource_id {
        store.get(id)
    } else if let Some(ds) = &request.datasource {
        Ok(ds.clone())
    } else {
        Err("Either datasource_id or datasource is required".into())
    }
}

/// POST /api/query
///
/// Execute a SQL query with RLS policies applied.
pub async fn handle_query(
    State(state): State<AppState>,
    Json(request): Json<QueryRequest>,
) -> (StatusCode, Json<QueryResponse>) {
    let request_id = request
        .request_id
        .clone()
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

    // Resolve datasource (from store or inline)
    let datasource = match resolve_datasource(&request, &state.datasource_store) {
        Ok(ds) => ds,
        Err(e) => {
            warn!("[{}] Datasource resolution failed: {}", request_id, e);
            return (
                StatusCode::BAD_REQUEST,
                Json(QueryResponse::error(&e, 0)),
            );
        }
    };

    info!(
        "[{}] Query request: sql_len={}, datasource={}:{}/{}, rls_policies={}",
        request_id,
        request.sql.len(),
        datasource.host,
        datasource.port,
        datasource.database,
        request.rls_policies.len(),
    );

    // Metadata queries (SHOW/DESC) bypass DataFusion — execute directly
    if is_metadata_query(&request.sql) {
        let response = execute_metadata_query(&state.pool_manager, &datasource, &request.sql).await;
        if response.error.is_some() {
            warn!("[{}] Metadata query failed: {:?}", request_id, response.error);
            return (StatusCode::BAD_REQUEST, Json(response));
        }
        info!(
            "[{}] Metadata query succeeded: {} rows in {}ms",
            request_id, response.row_count, response.execution_time_ms
        );
        return (StatusCode::OK, Json(response));
    }

    // Passthrough datasources (e.g. SLS) — execute directly, bypass DataFusion.
    // These datasources use PG wire protocol but have their own SQL engine,
    // making DataFusion's schema discovery incompatible.
    // RLS is still enforced via AST-level injection (best-effort).
    if is_passthrough_datasource(&datasource) {
        let response = execute_passthrough_with_rls(
            &state.pool_manager,
            &datasource,
            &request.sql,
            &request.rls_policies,
        )
        .await;
        if response.error.is_some() {
            warn!("[{}] Passthrough query failed: {:?}", request_id, response.error);
            return (StatusCode::BAD_REQUEST, Json(response));
        }
        info!(
            "[{}] Passthrough query ({}) succeeded: {} rows in {}ms (rls={})",
            request_id, datasource.db_type, response.row_count, response.execution_time_ms, response.rls_applied.len()
        );
        return (StatusCode::OK, Json(response));
    }

    // Doris pushdown — SQL uses Doris-specific functions that DataFusion cannot
    // execute. Parse + inject RLS via sqlparser, then run directly on Doris.
    if needs_pushdown(&datasource, &request.sql) {
        let response = execute_pushdown_query(
            &state.pool_manager,
            &datasource,
            &request.sql,
            &request.rls_policies,
        )
        .await;
        if response.error.is_some() {
            warn!("[{}] Doris pushdown query failed: {:?}", request_id, response.error);
            return (StatusCode::BAD_REQUEST, Json(response));
        }
        info!(
            "[{}] Doris pushdown query succeeded: {} rows in {}ms (rls={})",
            request_id, response.row_count, response.execution_time_ms, response.rls_applied.len()
        );
        return (StatusCode::OK, Json(response));
    }

    // Create per-request session with RLS-secured tables
    let session_result = QuerySession::create(
        &state.pool_manager,
        &datasource,
        &request.rls_policies,
    )
    .await;

    let (ctx, rls_applied) = match session_result {
        Ok(result) => result,
        Err(e) => {
            warn!("[{}] Session creation failed: {}", request_id, e);
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(QueryResponse::error(
                    &format!("Session creation failed: {}", e),
                    0,
                )),
            );
        }
    };

    // Execute the query
    let response = QueryExecutor::execute(&ctx, &request.sql, rls_applied).await;

    if response.error.is_some() {
        warn!("[{}] Query failed: {:?}", request_id, response.error);
        (StatusCode::BAD_REQUEST, Json(response))
    } else {
        info!(
            "[{}] Query succeeded: {} rows in {}ms",
            request_id, response.row_count, response.execution_time_ms
        );
        (StatusCode::OK, Json(response))
    }
}

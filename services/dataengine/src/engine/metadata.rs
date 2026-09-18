/// Metadata query execution — handles SHOW/DESC commands by executing
/// directly against the connection pool, bypassing DataFusion.
///
/// DataFusion is an analytical engine and does not implement MySQL/PG
/// metadata commands (SHOW TABLES, DESC table, etc.). This module
/// intercepts those commands and runs them against the real database.

use std::time::Instant;

use serde_json::Value;
use tracing::{info, warn};

use crate::providers::{ConnectionPoolManager, DbPool};
use crate::types::{ColumnInfo, DatasourceConfig, QueryResponse};

/// Check if SQL is a metadata query (SHOW or DESC).
pub fn is_metadata_query(sql: &str) -> bool {
    let upper = sql.trim().to_uppercase();
    upper.starts_with("SHOW") || upper.starts_with("DESC")
}

/// Check if datasource requires passthrough execution (bypass DataFusion).
/// SLS (Alibaba Cloud Log Service) uses PostgreSQL wire protocol but has its
/// own SQL engine — DataFusion's schema discovery is incompatible with it.
pub fn is_passthrough_datasource(config: &DatasourceConfig) -> bool {
    matches!(config.db_type.to_lowercase().as_str(), "sls")
}

/// Execute a metadata query directly against the database.
pub async fn execute_metadata_query(
    pool_manager: &ConnectionPoolManager,
    config: &DatasourceConfig,
    sql: &str,
) -> QueryResponse {
    let start = Instant::now();

    let pool = pool_manager.get_or_create(config).await;

    match &pool {
        DbPool::MySQL(pool) => {
            match execute_mysql_metadata(pool, sql).await {
                Ok((columns, rows)) => {
                    let elapsed = start.elapsed().as_millis() as u64;
                    let row_count = rows.len();
                    info!(
                        "Metadata query (MySQL) succeeded: {} rows in {}ms",
                        row_count, elapsed
                    );
                    QueryResponse {
                        columns,
                        rows,
                        row_count,
                        rls_applied: vec![],
                        execution_time_ms: elapsed,
                        error: None,
                    }
                }
                Err(e) => {
                    let elapsed = start.elapsed().as_millis() as u64;
                    warn!("Metadata query (MySQL) failed: {}", e);
                    QueryResponse::error(&format!("Metadata query failed: {}", e), elapsed)
                }
            }
        }
        DbPool::Postgres(pool) => {
            // SLS and other PG-compatible datasources use simple_query only
            let use_simple_only = is_passthrough_datasource(config);
            match execute_pg_metadata(pool, sql, use_simple_only).await {
                Ok((columns, rows)) => {
                    let elapsed = start.elapsed().as_millis() as u64;
                    let row_count = rows.len();
                    info!(
                        "Metadata query (Postgres) succeeded: {} rows in {}ms",
                        row_count, elapsed
                    );
                    QueryResponse {
                        columns,
                        rows,
                        row_count,
                        rls_applied: vec![],
                        execution_time_ms: elapsed,
                        error: None,
                    }
                }
                Err(e) => {
                    let elapsed = start.elapsed().as_millis() as u64;
                    warn!("Metadata query (Postgres) failed: {}", e);
                    QueryResponse::error(&format!("Metadata query failed: {}", e), elapsed)
                }
            }
        }
    }
}

/// Execute metadata query against MySQL pool.
async fn execute_mysql_metadata(
    pool: &mysql_async::Pool,
    sql: &str,
) -> Result<(Vec<ColumnInfo>, Vec<Vec<Value>>), String> {
    use mysql_async::prelude::*;

    let mut conn = pool
        .get_conn()
        .await
        .map_err(|e| format!("Connection failed: {}", e))?;

    // Query returns Vec<Row>; Row implements FromRow (identity).
    let rows: Vec<mysql_async::Row> = conn
        .query(sql)
        .await
        .map_err(|e| format!("Query failed: {}", e))?;

    if rows.is_empty() {
        return Ok((vec![], vec![]));
    }

    // Extract column names from first row
    let columns: Vec<ColumnInfo> = rows[0]
        .columns_ref()
        .iter()
        .map(|c| ColumnInfo {
            name: c.name_str().to_string(),
            data_type: "String".to_string(),
        })
        .collect();

    let num_cols = columns.len();
    let mut json_rows: Vec<Vec<Value>> = Vec::with_capacity(rows.len());

    for row in &rows {
        let mut json_row = Vec::with_capacity(num_cols);
        for i in 0..num_cols {
            let value = match row.as_ref(i) {
                Some(v) => mysql_value_to_json(v),
                None => Value::Null,
            };
            json_row.push(value);
        }
        json_rows.push(json_row);
    }

    Ok((columns, json_rows))
}

/// Convert a MySQL Value to JSON.
fn mysql_value_to_json(value: &mysql_async::Value) -> Value {
    match value {
        mysql_async::Value::NULL => Value::Null,
        mysql_async::Value::Bytes(b) => {
            Value::String(String::from_utf8_lossy(b).to_string())
        }
        mysql_async::Value::Int(i) => Value::Number((*i).into()),
        mysql_async::Value::UInt(u) => Value::Number((*u).into()),
        mysql_async::Value::Float(f) => {
            serde_json::to_value(f).unwrap_or(Value::Null)
        }
        mysql_async::Value::Double(d) => {
            serde_json::to_value(d).unwrap_or(Value::Null)
        }
        other => Value::String(format!("{:?}", other)),
    }
}

/// Execute metadata query against PostgreSQL pool.
/// If `simple_only` is true, uses simple_query directly (for SLS etc).
/// Otherwise, tries extended protocol first, falls back to simple_query.
///
/// Includes retry logic for transient connection failures (stale pool
/// connections dropped by the server). On retry, the old connection is
/// dropped and a fresh one is obtained from the pool.
async fn execute_pg_metadata(
    pool: &deadpool_postgres::Pool,
    sql: &str,
    simple_only: bool,
) -> Result<(Vec<ColumnInfo>, Vec<Vec<Value>>), String> {
    const MAX_RETRIES: usize = 2;

    for attempt in 0..=MAX_RETRIES {
        if attempt > 0 {
            warn!("Retrying PG metadata query (attempt {}/{})", attempt + 1, MAX_RETRIES + 1);
            tokio::time::sleep(std::time::Duration::from_millis(100 * attempt as u64)).await;
        }

        // Get a fresh connection from the pool (deadpool recycles stale ones).
        let client = pool
            .get()
            .await
            .map_err(|e| format!("Connection failed: {}", e))?;

        // Try extended protocol first (standard PG, provides column metadata)
        if !simple_only {
            let params: &[&(dyn tokio_postgres::types::ToSql + Sync)] = &[];
            match client.query(sql, params).await {
                Ok(rows) if !rows.is_empty() => {
                    let pg_columns = rows[0].columns();
                    let columns: Vec<ColumnInfo> = pg_columns
                        .iter()
                        .map(|c| ColumnInfo {
                            name: c.name().to_string(),
                            data_type: format!("{:?}", c.type_().name()),
                        })
                        .collect();

                    let num_cols = columns.len();
                    let mut json_rows: Vec<Vec<Value>> = Vec::with_capacity(rows.len());
                    for row in &rows {
                        let mut json_row = Vec::with_capacity(num_cols);
                        for (i, col) in pg_columns.iter().enumerate() {
                            let value = pg_column_to_json(row, i, col);
                            json_row.push(value);
                        }
                        json_rows.push(json_row);
                    }
                    return Ok((columns, json_rows));
                }
                Ok(_) => {
                    return Ok((vec![], vec![]));
                }
                Err(_) => {
                    // Extended protocol failed — fallback to simple_query below
                }
            }
        }

        // simple_query (works with SLS and other PG-compatible databases)
        match client.simple_query(sql).await {
            Ok(messages) => {
                // simple_query returns text values. Column names ARE available via
                // SimpleQueryRow::columns() (each SimpleQueryColumn has a name()), so we
                // preserve the real field names (e.g. SLS logstash: app_code, p_action, ...)
                // instead of generating generic col_N placeholders.
                let mut json_rows: Vec<Vec<Value>> = Vec::new();
                let mut num_cols = 0;
                let mut col_names: Vec<String> = Vec::new();

                for msg in &messages {
                    if let tokio_postgres::SimpleQueryMessage::Row(row) = msg {
                        if num_cols == 0 {
                            let cols = row.columns();
                            num_cols = cols.len();
                            col_names = cols.iter().map(|c| c.name().to_string()).collect();
                        }
                        let mut json_row = Vec::with_capacity(num_cols);
                        for i in 0..num_cols {
                            match row.try_get(i) {
                                Ok(Some(s)) => json_row.push(Value::String(s.to_string())),
                                Ok(None) => json_row.push(Value::Null),
                                Err(_) => json_row.push(Value::Null),
                            }
                        }
                        json_rows.push(json_row);
                    }
                }

                // Use real column names from simple_query; fall back to col_N only if absent
                // (e.g. empty result set where no row provided metadata).
                let columns: Vec<ColumnInfo> = (0..num_cols)
                    .map(|i| ColumnInfo {
                        name: col_names
                            .get(i)
                            .cloned()
                            .filter(|n| !n.is_empty())
                            .unwrap_or_else(|| format!("col_{}", i)),
                        data_type: "String".to_string(),
                    })
                    .collect();

                return Ok((columns, json_rows));
            }
            Err(e) => {
                // Capture the full error chain for diagnostics
                let mut full_error = e.to_string();
                let mut src = std::error::Error::source(&e);
                while let Some(cause) = src {
                    full_error.push_str(&format!(" -> {}", cause));
                    src = std::error::Error::source(cause);
                }

                let error_msg = format!("Query failed: {}", full_error);

                // Retry on connection-level failures (stale pool connections
                // dropped by server/firewall). The pool will recycle the dead
                // connection and provide a fresh one on the next get().
                let is_connection_error = [
                    "connection", "closed", "broken", "reset",
                    "db error", "unexpected", "eof", "timeout",
                ]
                .iter()
                .any(|k| full_error.to_lowercase().contains(k));

                if is_connection_error && attempt < MAX_RETRIES {
                    warn!(
                        "PG metadata connection error (attempt {}/{}): {}",
                        attempt + 1,
                        MAX_RETRIES + 1,
                        error_msg
                    );
                    // Drop the stale client; pool will create a new connection
                    drop(client);
                    continue;
                }

                return Err(error_msg);
            }
        }
    }

    Err("Query failed after all retries".to_string())
}

/// Convert a PostgreSQL row column to JSON by type.
fn pg_column_to_json(
    row: &tokio_postgres::Row,
    idx: usize,
    col: &tokio_postgres::Column,
) -> Value {
    use tokio_postgres::types::Type;

    let type_ = col.type_();
    match *type_ {
        Type::BOOL => row.get::<_, Option<bool>>(idx).map(Value::Bool).unwrap_or(Value::Null),
        Type::INT2 => row
            .get::<_, Option<i16>>(idx)
            .map(|v| Value::Number(v.into()))
            .unwrap_or(Value::Null),
        Type::INT4 => row
            .get::<_, Option<i32>>(idx)
            .map(|v| Value::Number(v.into()))
            .unwrap_or(Value::Null),
        Type::INT8 => row
            .get::<_, Option<i64>>(idx)
            .map(|v| Value::Number(v.into()))
            .unwrap_or(Value::Null),
        Type::FLOAT4 => row
            .get::<_, Option<f32>>(idx)
            .map(|v| serde_json::to_value(v).unwrap_or(Value::Null))
            .unwrap_or(Value::Null),
        Type::FLOAT8 => row
            .get::<_, Option<f64>>(idx)
            .map(|v| serde_json::to_value(v).unwrap_or(Value::Null))
            .unwrap_or(Value::Null),
        Type::TEXT | Type::VARCHAR | Type::BPCHAR | Type::NAME => row
            .get::<_, Option<String>>(idx)
            .map(Value::String)
            .unwrap_or(Value::Null),
        _ => {
            // Fallback: try String, then debug format
            match row.try_get::<_, Option<String>>(idx) {
                Ok(Some(s)) => Value::String(s),
                Ok(None) => Value::Null,
                Err(_) => Value::String(format!("{:?}", row.get::<_, Option<&str>>(idx))),
            }
        }
    }
}

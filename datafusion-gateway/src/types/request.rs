use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Gateway query request — sent from Python backend
#[derive(Debug, Clone, Deserialize)]
pub struct QueryRequest {
    pub sql: String,
    /// Use a stored datasource by ID (preferred, secure)
    pub datasource_id: Option<String>,
    /// Inline datasource config (backward compatible, less secure)
    pub datasource: Option<DatasourceConfig>,
    #[serde(default)]
    pub rls_policies: Vec<RLSPolicy>,
    /// Unique request ID for audit log correlation
    pub request_id: Option<String>,
}

/// Datasource connection configuration
#[derive(Debug, Clone, Deserialize)]
pub struct DatasourceConfig {
    pub db_type: String,        // "mysql" | "doris"
    pub host: String,
    pub port: u16,
    pub database: String,
    pub user: String,
    pub password: String,
    pub ssl: Option<bool>,
}

/// RLS policy for one or more tables
#[derive(Debug, Clone, Deserialize)]
pub struct RLSPolicy {
    /// Tables this policy applies to
    pub tables: Vec<String>,
    /// Row-level filter expression (SQL WHERE fragment)
    pub row_filter: String,
    /// Columns to hide completely
    pub hidden_columns: Vec<String>,
    /// Columns to mask: column_name -> mask_pattern
    pub masked_columns: HashMap<String, String>,
}

/// Gateway query response
#[derive(Debug, Serialize)]
pub struct QueryResponse {
    pub columns: Vec<ColumnInfo>,
    pub rows: Vec<Vec<serde_json::Value>>,
    pub row_count: usize,
    pub rls_applied: Vec<String>,
    pub execution_time_ms: u64,
    pub error: Option<String>,
}

/// Column metadata in response
#[derive(Debug, Clone, Serialize)]
pub struct ColumnInfo {
    pub name: String,
    pub data_type: String,
}

/// Health check response
#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status: String,
    pub version: String,
    pub uptime_seconds: u64,
}

/// Schema discovery response
#[derive(Debug, Serialize)]
#[allow(dead_code)]
pub struct SchemaResponse {
    pub tables: Vec<TableSchema>,
}

#[derive(Debug, Serialize)]
#[allow(dead_code)]
pub struct TableSchema {
    pub name: String,
    pub columns: Vec<ColumnSchema>,
}

#[derive(Debug, Serialize)]
#[allow(dead_code)]
pub struct ColumnSchema {
    pub name: String,
    pub data_type: String,
    pub nullable: bool,
    pub comment: String,
}

impl QueryResponse {
    pub fn error(msg: &str, elapsed_ms: u64) -> Self {
        Self {
            columns: vec![],
            rows: vec![],
            row_count: 0,
            rls_applied: vec![],
            execution_time_ms: elapsed_ms,
            error: Some(msg.to_string()),
        }
    }
}

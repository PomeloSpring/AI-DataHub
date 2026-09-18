use std::collections::HashMap;
use std::sync::Arc;

use arrow::datatypes::{DataType, Field, Schema, SchemaRef};
use datafusion::common::{DataFusionError, Result};
use mysql_async::prelude::*;
use mysql_async::{Pool, Row};
use tracing::{debug, info};

use crate::providers::pool_manager::DbPool;
use crate::providers::mysql_table::SqlDialect;

/// Table schema discovered from remote database
#[derive(Debug, Clone)]
pub struct DiscoveredTable {
    pub name: String,
    /// Schema qualifier: database name (MySQL/Doris) or schema name (Postgres).
    pub qualifier: String,
    pub columns: Vec<DiscoveredColumn>,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct DiscoveredColumn {
    pub name: String,
    pub data_type: String,
    pub nullable: bool,
    pub comment: String,
}

/// Schema discovery — fetches table metadata from INFORMATION_SCHEMA (MySQL/Doris)
/// or information_schema (Postgres).
pub struct SchemaDiscovery;

impl SchemaDiscovery {
    /// Discover all tables in a database
    pub async fn discover_all(
        pool: &DbPool,
        database: &str,
        _dialect: SqlDialect,
    ) -> Result<Vec<DiscoveredTable>> {
        match pool {
            DbPool::MySQL(mysql_pool) => discover_all_mysql(mysql_pool, database).await,
            DbPool::Postgres(pg_pool) => discover_all_postgres(pg_pool, database).await,
        }
    }
}

/// MySQL/Doris discovery via INFORMATION_SCHEMA.COLUMNS
async fn discover_all_mysql(pool: &Pool, database: &str) -> Result<Vec<DiscoveredTable>> {
    let sql = format!(
        "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_COMMENT \
         FROM INFORMATION_SCHEMA.COLUMNS \
         WHERE TABLE_SCHEMA = '{}' \
         ORDER BY TABLE_NAME, ORDINAL_POSITION",
        database.replace('\'', "''")
    );

    let mut conn = pool.get_conn().await.map_err(|e| {
        DataFusionError::Execution(format!("Failed to get connection: {}", e))
    })?;

    let rows: Vec<Row> = conn.query(&sql).await.map_err(|e| {
        DataFusionError::Execution(format!("Schema discovery query failed: {}", e))
    })?;

    let mut tables: HashMap<String, Vec<DiscoveredColumn>> = HashMap::new();

    for row in rows {
        let table_name: String = row.get("TABLE_NAME").unwrap_or_default();
        let column_name: String = row.get("COLUMN_NAME").unwrap_or_default();
        let data_type: String = row.get("DATA_TYPE").unwrap_or_default();
        let is_nullable: String = row.get("IS_NULLABLE").unwrap_or_default();
        let comment: String = row.get("COLUMN_COMMENT").unwrap_or_default();

        tables
            .entry(table_name.clone())
            .or_default()
            .push(DiscoveredColumn {
                name: column_name,
                data_type,
                nullable: is_nullable == "YES",
                comment,
            });
    }

    let result: Vec<DiscoveredTable> = tables
        .into_iter()
        .map(|(name, columns)| DiscoveredTable {
            qualifier: database.to_string(),
            name,
            columns,
        })
        .collect();

    info!(
        "Discovered {} tables in database '{}'",
        result.len(),
        database
    );
    Ok(result)
}

/// Postgres discovery via information_schema.columns (parameterized query).
/// Excludes system schemas; table qualifier is the schema name.
async fn discover_all_postgres(
    pool: &deadpool_postgres::Pool,
    database: &str,
) -> Result<Vec<DiscoveredTable>> {
    let client = pool.get().await.map_err(|e| {
        DataFusionError::Execution(format!("Failed to get connection: {}", e))
    })?;

    let sql = "SELECT table_schema, table_name, column_name, data_type, is_nullable \
               FROM information_schema.columns \
               WHERE table_catalog = $1 \
                 AND table_schema NOT IN ('pg_catalog', 'information_schema') \
               ORDER BY table_schema, table_name, ordinal_position";

    let rows = client.query(sql, &[&database]).await.map_err(|e| {
        DataFusionError::Execution(format!("Schema discovery query failed: {}", e))
    })?;

    // key: "schema.table" to avoid collisions across schemas
    let mut tables: HashMap<String, DiscoveredTable> = HashMap::new();

    for row in rows {
        let schema_name: String = row.get(0);
        let table_name: String = row.get(1);
        let column_name: String = row.get(2);
        let data_type: String = row.get(3);
        let is_nullable: String = row.get(4);

        let key = format!("{}.{}", schema_name, table_name);
        tables
            .entry(key)
            .or_insert_with(|| DiscoveredTable {
                name: table_name.clone(),
                qualifier: schema_name.clone(),
                columns: Vec::new(),
            })
            .columns
            .push(DiscoveredColumn {
                name: column_name,
                data_type,
                nullable: is_nullable == "YES",
                comment: String::new(),
            });
    }

    let result: Vec<DiscoveredTable> = tables.into_values().collect();

    info!(
        "Discovered {} tables in Postgres database '{}'",
        result.len(),
        database
    );
    Ok(result)
}

/// Convert MySQL data type string to Arrow DataType
pub fn mysql_type_to_arrow(mysql_type: &str) -> DataType {
    match mysql_type.to_lowercase().as_str() {
        "tinyint" => DataType::Int8,
        "smallint" => DataType::Int16,
        "int" | "integer" | "mediumint" => DataType::Int32,
        "bigint" => DataType::Int64,
        "float" => DataType::Float32,
        "double" | "real" => DataType::Float64,
        "decimal" | "numeric" => DataType::Float64, // Simplified
        "char" | "varchar" | "text" | "tinytext" | "mediumtext" | "longtext"
        | "enum" | "set" | "json" => DataType::Utf8,
        "date" => DataType::Utf8,       // mysql_async returns date as string
        "datetime" | "timestamp" => DataType::Utf8, // mysql_async returns datetime as string
        "time" => DataType::Utf8,       // Time as string
        "binary" | "varbinary" | "blob" | "tinyblob" | "mediumblob" | "longblob" => {
            DataType::Binary
        }
        "bit" => DataType::Boolean,
        _ => {
            debug!("Unknown MySQL type '{}', falling back to Utf8", mysql_type);
            DataType::Utf8
        }
    }
}

/// Convert Postgres data type string (information_schema.data_type) to Arrow DataType
pub fn pg_type_to_arrow(pg_type: &str) -> DataType {
    match pg_type.to_lowercase().as_str() {
        "smallint" | "int2" => DataType::Int16,
        "integer" | "int4" | "serial" => DataType::Int32,
        "bigint" | "int8" | "bigserial" => DataType::Int64,
        "real" | "float4" => DataType::Float32,
        "double precision" | "float8" => DataType::Float64,
        "numeric" | "decimal" | "money" => DataType::Float64, // Simplified
        "boolean" | "bool" => DataType::Boolean,
        "character varying" | "varchar" | "text" | "character" | "char" | "name"
        | "uuid" | "json" | "jsonb" | "inet" | "cidr" | "macaddr" => DataType::Utf8,
        // Dates/times come back as text via simple_query
        "date" | "time" | "time without time zone" | "time with time zone"
        | "timestamp" | "timestamp without time zone"
        | "timestamp with time zone" | "interval" => DataType::Utf8,
        "bytea" => DataType::Binary,
        _ => {
            debug!("Unknown Postgres type '{}', falling back to Utf8", pg_type);
            DataType::Utf8
        }
    }
}

/// Convert discovered table to Arrow Schema
pub fn table_to_arrow_schema(table: &DiscoveredTable, dialect: SqlDialect) -> SchemaRef {
    let fields: Vec<Field> = table
        .columns
        .iter()
        .map(|col| {
            let arrow_type = if dialect.is_postgres() {
                pg_type_to_arrow(&col.data_type)
            } else {
                mysql_type_to_arrow(&col.data_type)
            };
            Field::new(&col.name, arrow_type, col.nullable)
        })
        .collect();

    Arc::new(Schema::new(fields))
}

use std::collections::HashMap;
use std::sync::Arc;

use arrow::datatypes::{DataType, Field, Schema, SchemaRef};
use datafusion::common::{DataFusionError, Result};
use mysql_async::prelude::*;
use mysql_async::{Pool, Row};
use tracing::{debug, info};

/// Table schema discovered from remote database
#[derive(Debug, Clone)]
pub struct DiscoveredTable {
    pub name: String,
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

/// Schema discovery — fetches table metadata from INFORMATION_SCHEMA
pub struct SchemaDiscovery;

impl SchemaDiscovery {
    /// Discover all tables in a database
    pub async fn discover_all(pool: &Pool, database: &str) -> Result<Vec<DiscoveredTable>> {
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
            .map(|(name, columns)| DiscoveredTable { name, columns })
            .collect();

        info!(
            "Discovered {} tables in database '{}'",
            result.len(),
            database
        );
        Ok(result)
    }

    /// Discover a single table's schema
    #[allow(dead_code)]
    pub async fn discover_table(
        pool: &Pool,
        database: &str,
        table_name: &str,
    ) -> Result<Option<DiscoveredTable>> {
        let sql = format!(
            "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_COMMENT \
             FROM INFORMATION_SCHEMA.COLUMNS \
             WHERE TABLE_SCHEMA = '{}' AND TABLE_NAME = '{}' \
             ORDER BY ORDINAL_POSITION",
            database.replace('\'', "''"),
            table_name.replace('\'', "''")
        );

        let mut conn = pool.get_conn().await.map_err(|e| {
            DataFusionError::Execution(format!("Failed to get connection: {}", e))
        })?;

        let rows: Vec<Row> = conn.query(&sql).await.map_err(|e| {
            DataFusionError::Execution(format!("Schema discovery query failed: {}", e))
        })?;

        if rows.is_empty() {
            return Ok(None);
        }

        let columns: Vec<DiscoveredColumn> = rows
            .into_iter()
            .map(|row| {
                let data_type: String = row.get("DATA_TYPE").unwrap_or_default();
                let is_nullable: String = row.get("IS_NULLABLE").unwrap_or_default();
                DiscoveredColumn {
                    name: row.get("COLUMN_NAME").unwrap_or_default(),
                    data_type,
                    nullable: is_nullable == "YES",
                    comment: row.get("COLUMN_COMMENT").unwrap_or_default(),
                }
            })
            .collect();

        Ok(Some(DiscoveredTable {
            name: table_name.to_string(),
            columns,
        }))
    }
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

/// Convert discovered table to Arrow Schema
pub fn table_to_arrow_schema(table: &DiscoveredTable) -> SchemaRef {
    let fields: Vec<Field> = table
        .columns
        .iter()
        .map(|col| {
            let arrow_type = mysql_type_to_arrow(&col.data_type);
            Field::new(&col.name, arrow_type, col.nullable)
        })
        .collect();

    Arc::new(Schema::new(fields))
}

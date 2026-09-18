use std::any::Any;
use std::fmt;
use std::sync::Arc;

use arrow::array::*;
use arrow::datatypes::{DataType, SchemaRef};
use arrow::record_batch::RecordBatch;
use async_trait::async_trait;
use datafusion::arrow;
use datafusion::common::{DataFusionError, Result};
use datafusion::execution::context::TaskContext;
use datafusion::physical_plan::{
    DisplayAs, DisplayFormatType, ExecutionPlan, Partitioning, PlanProperties,
    SendableRecordBatchStream, stream::RecordBatchStreamAdapter,
};
use futures::stream;
use mysql_async::prelude::*;
use mysql_async::Row;
use tracing::{debug, warn};

use super::pool_manager::DbPool;

/// Remote SQL execution plan — executes a SQL query against a remote MySQL/Doris/Postgres
/// database and returns the results as Arrow RecordBatch streams.
#[derive(Debug)]
#[allow(dead_code)]
pub struct RemoteSqlExec {
    pool: DbPool,
    sql: String,
    schema: SchemaRef,
    projected_schema: SchemaRef,
    properties: PlanProperties,
}

impl RemoteSqlExec {
    pub fn new(pool: DbPool, sql: String, schema: SchemaRef, projection: Option<Vec<usize>>) -> Self {
        let projected_schema = if let Some(ref proj) = projection {
            let fields: Vec<_> = proj.iter()
                .map(|i| schema.field(*i).clone())
                .collect();
            Arc::new(arrow::datatypes::Schema::new(fields))
        } else {
            schema.clone()
        };

        let properties = PlanProperties::new(
            datafusion::physical_expr::EquivalenceProperties::new(projected_schema.clone()),
            Partitioning::UnknownPartitioning(1),
            datafusion::physical_plan::ExecutionMode::Bounded,
        );

        Self {
            pool,
            sql,
            schema,
            projected_schema,
            properties,
        }
    }
}

impl DisplayAs for RemoteSqlExec {
    fn fmt_as(&self, _t: DisplayFormatType, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "RemoteSqlExec: {}", self.sql)
    }
}

#[async_trait]
impl ExecutionPlan for RemoteSqlExec {
    fn name(&self) -> &str {
        "RemoteSqlExec"
    }

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn schema(&self) -> SchemaRef {
        self.projected_schema.clone()
    }

    fn properties(&self) -> &PlanProperties {
        &self.properties
    }

    fn children(&self) -> Vec<&Arc<dyn ExecutionPlan>> {
        vec![]
    }

    fn with_new_children(
        self: Arc<Self>,
        _children: Vec<Arc<dyn ExecutionPlan>>,
    ) -> Result<Arc<dyn ExecutionPlan>> {
        Ok(self)
    }

    fn execute(
        &self,
        _partition: usize,
        _context: Arc<TaskContext>,
    ) -> Result<SendableRecordBatchStream> {
        let pool = self.pool.clone();
        let sql = self.sql.clone();
        let schema = self.projected_schema.clone();
        let schema_for_stream = schema.clone();

        debug!("Executing remote SQL: {}", sql);

        // Create a stream that executes the query asynchronously
        let stream = stream::once(async move {
            match pool {
                DbPool::MySQL(mysql_pool) => {
                    // Get connection and execute
                    let mut conn = mysql_pool.get_conn().await.map_err(|e| {
                        DataFusionError::Execution(format!("Failed to get connection: {}", e))
                    })?;

                    let rows: Vec<Row> = conn.query(sql).await.map_err(|e| {
                        DataFusionError::Execution(format!("Remote SQL failed: {}", e))
                    })?;

                    rows_to_record_batch(rows, schema_for_stream)
                }
                DbPool::Postgres(pg_pool) => {
                    let client = pg_pool.get().await.map_err(|e| {
                        DataFusionError::Execution(format!("Failed to get connection: {}", e))
                    })?;

                    // simple_query returns all values as text, which we parse
                    // according to the Arrow schema.
                    let rows = client.simple_query(&sql).await.map_err(|e| {
                        DataFusionError::Execution(format!("Remote SQL failed: {}", e))
                    })?;

                    pg_rows_to_record_batch(rows, schema_for_stream)
                }
            }
        });

        Ok(Box::pin(RecordBatchStreamAdapter::new(schema, stream)))
    }
}

/// Convert MySQL rows to Arrow RecordBatch
fn rows_to_record_batch(rows: Vec<Row>, schema: SchemaRef) -> Result<RecordBatch> {
    if rows.is_empty() {
        return Ok(RecordBatch::new_empty(schema));
    }

    let num_rows = rows.len();
    let num_cols = schema.fields().len();

    // Zero-column batches (e.g. COUNT(*)) require an explicit row count
    if num_cols == 0 {
        let options = arrow::record_batch::RecordBatchOptions::new()
            .with_row_count(Some(num_rows));
        return RecordBatch::try_new_with_options(schema, vec![], &options)
            .map_err(|e| DataFusionError::Execution(format!("Failed to build RecordBatch: {}", e)));
    }

    let mut columns: Vec<ArrayRef> = Vec::with_capacity(num_cols);

    for col_idx in 0..num_cols {
        let field = schema.field(col_idx);
        let array = build_column_array(&rows, col_idx, field.data_type(), num_rows)?;
        columns.push(array);
    }

    RecordBatch::try_new(schema, columns)
        .map_err(|e| DataFusionError::Execution(format!("Failed to build RecordBatch: {}", e)))
}

/// Build an Arrow array for one column from MySQL rows
fn build_column_array(
    rows: &[Row],
    col_idx: usize,
    data_type: &DataType,
    num_rows: usize,
) -> Result<ArrayRef> {
    match data_type {
        DataType::Utf8 => {
            let mut builder = StringBuilder::with_capacity(num_rows, num_rows * 32);
            for row in rows {
                // mysql_async Row uses get by index, returns Option<Value>
                let val: Option<String> = row.get(col_idx).unwrap_or(None);
                match val {
                    Some(v) => builder.append_value(&v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
        DataType::Int8 => {
            let mut builder = Int8Builder::with_capacity(num_rows);
            for row in rows {
                let val: Option<i8> = row.get(col_idx).unwrap_or(None);
                match val {
                    Some(v) => builder.append_value(v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
        DataType::Int16 => {
            let mut builder = Int16Builder::with_capacity(num_rows);
            for row in rows {
                let val: Option<i16> = row.get(col_idx).unwrap_or(None);
                match val {
                    Some(v) => builder.append_value(v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
        DataType::Int32 => {
            let mut builder = Int32Builder::with_capacity(num_rows);
            for row in rows {
                let val: Option<i32> = row.get(col_idx).unwrap_or(None);
                match val {
                    Some(v) => builder.append_value(v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
        DataType::Int64 => {
            let mut builder = Int64Builder::with_capacity(num_rows);
            for row in rows {
                let val: Option<i64> = row.get(col_idx).unwrap_or(None);
                match val {
                    Some(v) => builder.append_value(v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
        DataType::Float32 => {
            let mut builder = Float32Builder::with_capacity(num_rows);
            for row in rows {
                let val: Option<f32> = row.get(col_idx).unwrap_or(None);
                match val {
                    Some(v) => builder.append_value(v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
        DataType::Float64 => {
            let mut builder = Float64Builder::with_capacity(num_rows);
            for row in rows {
                let val: Option<f64> = row.get(col_idx).unwrap_or(None);
                match val {
                    Some(v) => builder.append_value(v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
        DataType::Boolean => {
            let mut builder = BooleanBuilder::with_capacity(num_rows);
            for row in rows {
                let val: Option<bool> = row.get(col_idx).unwrap_or(None);
                match val {
                    Some(v) => builder.append_value(v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
        // Fallback: convert everything to string
        _ => {
            warn!("Unsupported data type {:?} for column {}, falling back to Utf8", data_type, col_idx);
            let mut builder = StringBuilder::with_capacity(num_rows, num_rows * 32);
            for row in rows {
                let val: Option<String> = row.get(col_idx).unwrap_or(None);
                match val {
                    Some(v) => builder.append_value(&v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
    }
}

// ── PostgreSQL (simple_query returns text values) ────────────────────────────────────

/// Convert Postgres simple_query results (all text) to Arrow RecordBatch
fn pg_rows_to_record_batch(
    messages: Vec<tokio_postgres::SimpleQueryMessage>,
    schema: SchemaRef,
) -> Result<RecordBatch> {
    // simple_query results include a command-tag message (e.g. "SELECT 5") at the end;
    // only keep actual data rows with matching column count.
    let num_cols = schema.fields().len();
    let rows: Vec<tokio_postgres::row::SimpleQueryRow> = messages
        .into_iter()
        .filter_map(|m| match m {
            tokio_postgres::SimpleQueryMessage::Row(r) if r.columns().len() >= num_cols => Some(r),
            _ => None,
        })
        .collect();

    if rows.is_empty() {
        let options = arrow::record_batch::RecordBatchOptions::new()
            .with_row_count(Some(0));
        return RecordBatch::try_new_with_options(schema, vec![], &options)
            .map_err(|e| DataFusionError::Execution(format!("Failed to build RecordBatch: {}", e)));
    }

    let num_rows = rows.len();
    let mut columns: Vec<ArrayRef> = Vec::with_capacity(num_cols);

    for col_idx in 0..num_cols {
        let field = schema.field(col_idx);
        let array = build_pg_column_array(&rows, col_idx, field.data_type(), num_rows)?;
        columns.push(array);
    }

    // Zero-column batches (e.g. COUNT(*)) require an explicit row count
    if num_cols == 0 {
        let options = arrow::record_batch::RecordBatchOptions::new()
            .with_row_count(Some(num_rows));
        return RecordBatch::try_new_with_options(schema, columns, &options)
            .map_err(|e| DataFusionError::Execution(format!("Failed to build RecordBatch: {}", e)));
    }

    RecordBatch::try_new(schema, columns)
        .map_err(|e| DataFusionError::Execution(format!("Failed to build RecordBatch: {}", e)))
}

/// Build an Arrow array for one column from Postgres text values
fn build_pg_column_array(
    rows: &[tokio_postgres::row::SimpleQueryRow],
    col_idx: usize,
    data_type: &DataType,
    num_rows: usize,
) -> Result<ArrayRef> {
    macro_rules! build_numeric {
        ($builder_ty:ident, $val_ty:ty) => {{
            let mut builder = $builder_ty::with_capacity(num_rows);
            for row in rows {
                match row.try_get::<usize>(col_idx).ok().flatten() {
                    Some(v) => match v.parse::<$val_ty>() {
                        Ok(n) => builder.append_value(n),
                        Err(_) => builder.append_null(),
                    },
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()) as ArrayRef)
        }};
    }

    match data_type {
        DataType::Boolean => {
            let mut builder = BooleanBuilder::with_capacity(num_rows);
            for row in rows {
                // try_get returns Err for out-of-range indices (defensive, e.g. zero-column scans)
                match row.try_get::<usize>(col_idx).ok().flatten() {
                    // Postgres text format for bool is 't' / 'f'
                    Some(v) => builder.append_value(matches!(v, "t" | "true" | "TRUE" | "1")),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
        DataType::Int8 => build_numeric!(Int8Builder, i8),
        DataType::Int16 => build_numeric!(Int16Builder, i16),
        DataType::Int32 => build_numeric!(Int32Builder, i32),
        DataType::Int64 => build_numeric!(Int64Builder, i64),
        DataType::Float32 => build_numeric!(Float32Builder, f32),
        DataType::Float64 => build_numeric!(Float64Builder, f64),
        // Everything else (Utf8, dates/timestamps rendered as text, fallback)
        _ => {
            let mut builder = StringBuilder::with_capacity(num_rows, num_rows * 32);
            for row in rows {
                match row.try_get::<usize>(col_idx).ok().flatten() {
                    Some(v) => builder.append_value(v),
                    None => builder.append_null(),
                }
            }
            Ok(Arc::new(builder.finish()))
        }
    }
}

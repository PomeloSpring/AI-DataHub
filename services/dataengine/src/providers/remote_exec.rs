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
use mysql_async::{Pool, Row};
use tracing::{debug, warn};

/// Remote SQL execution plan — executes a SQL query against a remote MySQL/Doris database
/// and returns the results as Arrow RecordBatch streams.
#[derive(Debug)]
#[allow(dead_code)]
pub struct RemoteSqlExec {
    pool: Pool,
    sql: String,
    schema: SchemaRef,
    projected_schema: SchemaRef,
    properties: PlanProperties,
}

impl RemoteSqlExec {
    pub fn new(pool: Pool, sql: String, schema: SchemaRef, projection: Option<Vec<usize>>) -> Self {
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
            // Get connection and execute
            let mut conn = pool.get_conn().await.map_err(|e| {
                DataFusionError::Execution(format!("Failed to get connection: {}", e))
            })?;

            let rows: Vec<Row> = conn.query(sql).await.map_err(|e| {
                DataFusionError::Execution(format!("Remote SQL failed: {}", e))
            })?;

            let batch = rows_to_record_batch(rows, schema_for_stream)?;
            Ok(batch)
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

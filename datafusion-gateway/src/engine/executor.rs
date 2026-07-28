use std::time::Instant;

use arrow::array::Array;
use arrow::record_batch::RecordBatch;
use datafusion::prelude::SessionContext;
use serde_json::Value;
use tracing::warn;

use crate::types::{ColumnInfo, QueryResponse};

/// Query executor — runs SQL against a DataFusion session and converts results to JSON
pub struct QueryExecutor;

impl QueryExecutor {
    /// Execute SQL and return a QueryResponse
    pub async fn execute(
        ctx: &SessionContext,
        sql: &str,
        rls_applied: Vec<String>,
    ) -> QueryResponse {
        let start = Instant::now();

        // Execute the query
        let result = ctx.sql(sql).await;

        let df = match result {
            Ok(df) => df,
            Err(e) => {
                let elapsed = start.elapsed().as_millis() as u64;
                warn!("SQL execution failed: {}", e);
                return QueryResponse::error(&format!("SQL execution failed: {}", e), elapsed);
            }
        };

        // Collect results
        let batches = match df.collect().await {
            Ok(batches) => batches,
            Err(e) => {
                let elapsed = start.elapsed().as_millis() as u64;
                warn!("Result collection failed: {}", e);
                return QueryResponse::error(&format!("Result collection failed: {}", e), elapsed);
            }
        };

        let elapsed = start.elapsed().as_millis() as u64;

        // Convert to response
        batches_to_response(batches, rls_applied, elapsed)
    }
}

/// Convert Arrow RecordBatches to QueryResponse
fn batches_to_response(
    batches: Vec<RecordBatch>,
    rls_applied: Vec<String>,
    elapsed_ms: u64,
) -> QueryResponse {
    if batches.is_empty() {
        return QueryResponse {
            columns: vec![],
            rows: vec![],
            row_count: 0,
            rls_applied,
            execution_time_ms: elapsed_ms,
            error: None,
        };
    }

    // Extract column info from first batch
    let schema = batches[0].schema();
    let columns: Vec<ColumnInfo> = schema
        .fields()
        .iter()
        .map(|f| ColumnInfo {
            name: f.name().clone(),
            data_type: format!("{:?}", f.data_type()),
        })
        .collect();

    let num_cols = columns.len();
    let mut rows: Vec<Vec<Value>> = Vec::new();

    for batch in &batches {
        let num_rows = batch.num_rows();

        for row_idx in 0..num_rows {
            let mut row: Vec<Value> = Vec::with_capacity(num_cols);

            for col_idx in 0..num_cols {
                let array = batch.column(col_idx);
                let value = array_value_to_json(array.as_ref(), row_idx);
                row.push(value);
            }

            rows.push(row);
        }
    }

    let row_count = rows.len();

    QueryResponse {
        columns,
        rows,
        row_count,
        rls_applied,
        execution_time_ms: elapsed_ms,
        error: None,
    }
}

/// Convert a single Arrow array value to JSON
fn array_value_to_json(array: &dyn Array, row_idx: usize) -> Value {
    if array.is_null(row_idx) {
        return Value::Null;
    }

    match array.data_type() {
        arrow::datatypes::DataType::Boolean => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::BooleanArray>()
                .unwrap();
            Value::Bool(arr.value(row_idx))
        }
        arrow::datatypes::DataType::Int8 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::Int8Array>()
                .unwrap();
            Value::Number(arr.value(row_idx).into())
        }
        arrow::datatypes::DataType::Int16 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::Int16Array>()
                .unwrap();
            Value::Number(arr.value(row_idx).into())
        }
        arrow::datatypes::DataType::Int32 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::Int32Array>()
                .unwrap();
            Value::Number(arr.value(row_idx).into())
        }
        arrow::datatypes::DataType::Int64 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::Int64Array>()
                .unwrap();
            Value::Number(arr.value(row_idx).into())
        }
        arrow::datatypes::DataType::UInt8 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::UInt8Array>()
                .unwrap();
            Value::Number(arr.value(row_idx).into())
        }
        arrow::datatypes::DataType::UInt16 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::UInt16Array>()
                .unwrap();
            Value::Number(arr.value(row_idx).into())
        }
        arrow::datatypes::DataType::UInt32 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::UInt32Array>()
                .unwrap();
            Value::Number(arr.value(row_idx).into())
        }
        arrow::datatypes::DataType::UInt64 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::UInt64Array>()
                .unwrap();
            // JSON doesn't support u64 max, use string for large values
            let val = arr.value(row_idx);
            serde_json::to_value(val).unwrap_or(Value::String(val.to_string()))
        }
        arrow::datatypes::DataType::Float32 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::Float32Array>()
                .unwrap();
            serde_json::to_value(arr.value(row_idx)).unwrap_or(Value::Null)
        }
        arrow::datatypes::DataType::Float64 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::Float64Array>()
                .unwrap();
            serde_json::to_value(arr.value(row_idx)).unwrap_or(Value::Null)
        }
        arrow::datatypes::DataType::Utf8 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::StringArray>()
                .unwrap();
            Value::String(arr.value(row_idx).to_string())
        }
        arrow::datatypes::DataType::LargeUtf8 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::LargeStringArray>()
                .unwrap();
            Value::String(arr.value(row_idx).to_string())
        }
        arrow::datatypes::DataType::Date32 => {
            let arr = array
                .as_any()
                .downcast_ref::<arrow::array::Date32Array>()
                .unwrap();
            let days = arr.value(row_idx);
            let date = chrono::NaiveDate::from_ymd_opt(1970, 1, 1)
                .unwrap()
                .checked_add_days(chrono::Days::new(days as u64))
                .unwrap();
            Value::String(date.format("%Y-%m-%d").to_string())
        }
        arrow::datatypes::DataType::Timestamp(unit, _tz) => {
            let millis = match unit {
                arrow::datatypes::TimeUnit::Second => {
                    let arr = array
                        .as_any()
                        .downcast_ref::<arrow::array::TimestampSecondArray>()
                        .unwrap();
                    arr.value(row_idx) * 1000
                }
                arrow::datatypes::TimeUnit::Millisecond => {
                    let arr = array
                        .as_any()
                        .downcast_ref::<arrow::array::TimestampMillisecondArray>()
                        .unwrap();
                    arr.value(row_idx)
                }
                arrow::datatypes::TimeUnit::Microsecond => {
                    let arr = array
                        .as_any()
                        .downcast_ref::<arrow::array::TimestampMicrosecondArray>()
                        .unwrap();
                    arr.value(row_idx) / 1000
                }
                arrow::datatypes::TimeUnit::Nanosecond => {
                    let arr = array
                        .as_any()
                        .downcast_ref::<arrow::array::TimestampNanosecondArray>()
                        .unwrap();
                    arr.value(row_idx) / 1_000_000
                }
            };
            let dt = chrono::DateTime::from_timestamp_millis(millis);
            match dt {
                Some(dt) => Value::String(dt.format("%Y-%m-%d %H:%M:%S").to_string()),
                None => Value::Null,
            }
        }
        _ => {
            warn!("Unsupported Arrow type: {:?}, using debug format", array.data_type());
            Value::String(format!("{:?}", array))
        }
    }
}

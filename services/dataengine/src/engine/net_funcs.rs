//! MySQL 兼容网络函数（INET_ATON / INET_NTOA）—— DataFusion 内置 UDF 注册。
//!
//! DataFusion 标准函数集不含 MySQL 的网络函数，ip2geo 等 lookup 型 UDF
//! 的展开模板会引用 INET_ATON；在此以引擎级 UDF 补齐，保证 SQL 下发
//! DataEngine 即可执行（不走任何直连旁路，安全红线见 security-guardrails §1）。

use std::sync::Arc;

use arrow::array::{Array, ArrayRef, Int64Array, StringArray, UInt64Array};
use arrow::datatypes::DataType;
use datafusion::common::{DataFusionError, Result};
use datafusion::logical_expr::{ColumnarValue, ScalarUDF, Volatility};
use datafusion::prelude::create_udf;

/// "a.b.c.d" → u32 数值；非法返回 None（SQL 语义 NULL）
fn parse_ipv4(s: &str) -> Option<i64> {
    let parts: Vec<&str> = s.trim().split('.').collect();
    if parts.len() != 4 {
        return None;
    }
    let mut acc: i64 = 0;
    for p in parts {
        let v: i64 = p.parse().ok()?;
        if !(0..=255).contains(&v) {
            return None;
        }
        acc = acc * 256 + v;
    }
    Some(acc)
}

fn format_ipv4(v: i64) -> Option<String> {
    if !(0..=4294967295_i64).contains(&v) {
        return None;
    }
    Some(format!(
        "{}.{}.{}.{}",
        (v >> 24) & 0xff,
        (v >> 16) & 0xff,
        (v >> 8) & 0xff,
        v & 0xff
    ))
}

fn as_string_array(values: &[ColumnarValue]) -> Result<ArrayRef> {
    if values.len() != 1 {
        return Err(DataFusionError::Internal("inet_aton 需要 1 个参数".into()));
    }
    values[0].clone().into_array(1)
}

fn eval_inet_aton(values: &[ColumnarValue]) -> Result<ColumnarValue> {
    let arr = as_string_array(values)?;
    let out: Int64Array = match arr.data_type() {
        DataType::Utf8 => {
            let sa = arr
                .as_any()
                .downcast_ref::<StringArray>()
                .ok_or_else(|| DataFusionError::Internal("inet_aton 参数类型错误".into()))?;
            sa.iter().map(|v| v.and_then(parse_ipv4)).collect()
        }
        DataType::LargeUtf8 => {
            let sa = arr
                .as_any()
                .downcast_ref::<arrow::array::LargeStringArray>()
                .ok_or_else(|| DataFusionError::Internal("inet_aton 参数类型错误".into()))?;
            sa.iter().map(|v| v.and_then(parse_ipv4)).collect()
        }
        DataType::Null => Int64Array::new_null(arr.len()),
        other => {
            return Err(DataFusionError::Internal(format!(
                "inet_aton 不支持参数类型 {other:?}"
            )))
        }
    };
    Ok(ColumnarValue::Array(Arc::new(out)))
}

fn eval_inet_ntoa(values: &[ColumnarValue]) -> Result<ColumnarValue> {
    let arr = as_string_array(values)?;
    let out: StringArray = match arr.data_type() {
        DataType::Int64 => {
            let ia = arr
                .as_any()
                .downcast_ref::<Int64Array>()
                .ok_or_else(|| DataFusionError::Internal("inet_ntoa 参数类型错误".into()))?;
            ia.iter().map(|v| v.and_then(format_ipv4)).collect()
        }
        DataType::UInt64 => {
            let ia = arr
                .as_any()
                .downcast_ref::<UInt64Array>()
                .ok_or_else(|| DataFusionError::Internal("inet_ntoa 参数类型错误".into()))?;
            ia.iter()
                .map(|v| v.and_then(|x| format_ipv4(x as i64)))
                .collect()
        }
        DataType::Null => StringArray::new_null(arr.len()),
        other => {
            return Err(DataFusionError::Internal(format!(
                "inet_ntoa 不支持参数类型 {other:?}"
            )))
        }
    };
    Ok(ColumnarValue::Array(Arc::new(out)))
}

/// MySQL 兼容网络函数集（可在 session 构建时批量注册）
///
/// 执行位置：函数计算在 DataFusion 层完成，表数据经 RemoteSqlTable
/// 按需下推（列裁剪/过滤下推）拉取，不向数据源下推 UDF 计算。
pub fn net_udfs() -> Vec<ScalarUDF> {
    vec![
        create_udf(
            "inet_aton",
            vec![DataType::Utf8],
            Arc::new(DataType::Int64),
            Volatility::Immutable,
            Arc::new(eval_inet_aton),
        ),
        create_udf(
            "inet_ntoa",
            vec![DataType::Int64],
            Arc::new(DataType::Utf8),
            Volatility::Immutable,
            Arc::new(eval_inet_ntoa),
        ),
    ]
}

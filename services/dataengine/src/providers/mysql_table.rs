use std::any::Any;
use std::fmt;
use std::sync::Arc;

use arrow::datatypes::SchemaRef;
use async_trait::async_trait;
use datafusion::catalog::Session;
use datafusion::common::Result;
use datafusion::datasource::TableProvider;
use datafusion::logical_expr::{Expr, TableProviderFilterPushDown, TableType, Operator};
use datafusion::physical_plan::ExecutionPlan;
use tracing::debug;

use super::pool_manager::DbPool;
use super::remote_exec::RemoteSqlExec;

/// SQL dialect for remote database
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SqlDialect {
    MySQL,
    Doris,
    Postgres,
}

impl SqlDialect {
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "doris" => Self::Doris,
            "postgres" | "postgresql" | "pg" => Self::Postgres,
            _ => Self::MySQL,
        }
    }

    pub fn is_postgres(&self) -> bool {
        matches!(self, Self::Postgres)
    }

    /// Quote an identifier
    pub fn quote_identifier(&self, name: &str) -> String {
        match self {
            Self::Postgres => format!("\"{}\"", name.replace('\"', "\"\"")),
            Self::Doris | Self::MySQL => format!("`{}`", name.replace('`', "``")),
        }
    }
}

/// Remote MySQL/Doris/Postgres table provider.
///
/// Translates DataFusion query plans into remote SQL that gets pushed down
/// to the underlying database for execution.
pub struct RemoteSqlTable {
    pool: DbPool,
    /// Qualifier for the table: database name (MySQL/Doris) or schema name (Postgres).
    qualifier: String,
    table_name: String,
    schema: SchemaRef,
    dialect: SqlDialect,
}

impl fmt::Debug for RemoteSqlTable {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RemoteSqlTable")
            .field("qualifier", &self.qualifier)
            .field("table_name", &self.table_name)
            .field("dialect", &self.dialect)
            .finish()
    }
}

impl RemoteSqlTable {
    pub fn new(
        pool: DbPool,
        qualifier: String,
        table_name: String,
        schema: SchemaRef,
        dialect: SqlDialect,
    ) -> Self {
        Self {
            pool,
            qualifier,
            table_name,
            schema,
            dialect,
        }
    }
}

#[async_trait]
impl TableProvider for RemoteSqlTable {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn schema(&self) -> SchemaRef {
        self.schema.clone()
    }

    fn table_type(&self) -> TableType {
        TableType::Base
    }

    async fn scan(
        &self,
        _ctx: &dyn Session,
        projection: Option<&Vec<usize>>,
        filters: &[Expr],
        limit: Option<usize>,
    ) -> Result<Arc<dyn ExecutionPlan>> {
        // 1. Build SELECT columns (projection pushdown)
        let mut columns = if let Some(proj) = projection {
            proj.iter()
                .map(|i| {
                    let field = self.schema.field(*i);
                    self.dialect.quote_identifier(field.name())
                })
                .collect::<Vec<_>>()
        } else {
            vec!["*".to_string()]
        };

        // Empty projection (e.g. COUNT(*)): select a constant so the remote
        // SQL stays valid and still returns one row per table row.
        if columns.is_empty() {
            columns = vec!["1".to_string()];
        }

        // 2. Build WHERE clause (predicate pushdown)
        //    Only push filters that translate *fully* to remote SQL. Filters that
        //    contain expressions we cannot faithfully unparse (date arithmetic like
        //    `CURRENT_DATE - INTERVAL '7 days'`, `CAST`, scalar functions such as
        //    `now()`) are skipped here. They were reported as `Inexact` in
        //    `supports_filters_pushdown`, so DataFusion re-applies them locally after
        //    the scan — correctness is preserved and we never emit `/* unsupported */`
        //    into the remote SQL (which would break the remote parser).
        let pushable: Vec<String> = filters
            .iter()
            .filter_map(|expr| try_expr_to_sql(expr, &self.dialect))
            .collect();
        let where_clause = if pushable.is_empty() {
            String::new()
        } else {
            format!(" WHERE {}", pushable.join(" AND "))
        };

        // 3. Build LIMIT clause
        let limit_clause = limit
            .map(|n| format!(" LIMIT {}", n))
            .unwrap_or_default();

        // 4. Assemble full SQL
        let remote_sql = format!(
            "SELECT {} FROM {}.{}{}{}",
            columns.join(", "),
            self.dialect.quote_identifier(&self.qualifier),
            self.dialect.quote_identifier(&self.table_name),
            where_clause,
            limit_clause,
        );

        debug!("Pushdown SQL: {}", remote_sql);

        // 5. Return remote execution plan
        Ok(Arc::new(RemoteSqlExec::new(
            self.pool.clone(),
            remote_sql,
            self.schema.clone(),
            projection.cloned(),
        )))
    }

    fn supports_filters_pushdown(
        &self,
        filters: &[&Expr],
    ) -> Result<Vec<TableProviderFilterPushDown>> {
        // A filter is pushed down as `Exact` only when it can be fully translated to
        // remote SQL. Otherwise it is `Inexact`: DataFusion keeps the predicate and
        // re-applies it locally after the scan, so skipping it in `scan()` is safe.
        Ok(filters
            .iter()
            .map(|f| {
                if try_expr_to_sql(f, &self.dialect).is_some() {
                    TableProviderFilterPushDown::Exact
                } else {
                    TableProviderFilterPushDown::Inexact
                }
            })
            .collect())
    }
}

/// Try to convert a DataFusion `Expr` into a remote SQL fragment.
///
/// Returns `None` when the expression (or any sub-expression) cannot be
/// faithfully translated — e.g. scalar functions (`now()`, `current_date`),
/// `CAST`, or `INTERVAL` literals used in date arithmetic. Callers MUST treat
/// `None` as "do not push down": DataFusion evaluates such predicates locally
/// (they are reported as `Inexact`), which keeps results correct without ever
/// emitting invalid remote SQL like `/* unsupported */`.
fn try_expr_to_sql(expr: &Expr, dialect: &SqlDialect) -> Option<String> {
    match expr {
        Expr::Column(col) => {
            // Pushdown SQL is always single-table, so the relation qualifier is
            // omitted (it may also be a parser placeholder like "_temp" from
            // RLS filter parsing, which doesn't exist on the remote database).
            Some(dialect.quote_identifier(&col.name))
        }
        Expr::Literal(val) => try_literal_to_sql(val),
        Expr::BinaryExpr(binary) => {
            let op_str = match binary.op {
                Operator::Eq => "=",
                Operator::NotEq => "!=",
                Operator::Lt => "<",
                Operator::LtEq => "<=",
                Operator::Gt => ">",
                Operator::GtEq => ">=",
                Operator::And => "AND",
                Operator::Or => "OR",
                Operator::Plus => "+",
                Operator::Minus => "-",
                Operator::Multiply => "*",
                Operator::Divide => "/",
                Operator::Modulo => "%",
                _ => {
                    debug!("Non-pushable operator: {:?}", binary.op);
                    return None;
                }
            };
            let left = try_expr_to_sql(&binary.left, dialect)?;
            let right = try_expr_to_sql(&binary.right, dialect)?;
            Some(format!("({} {} {})", left, op_str, right))
        }
        Expr::IsNull(inner) => Some(format!("({} IS NULL)", try_expr_to_sql(inner, dialect)?)),
        Expr::IsNotNull(inner) => {
            Some(format!("({} IS NOT NULL)", try_expr_to_sql(inner, dialect)?))
        }
        Expr::Negative(inner) => Some(format!("(-{})", try_expr_to_sql(inner, dialect)?)),
        Expr::Not(inner) => Some(format!("(NOT {})", try_expr_to_sql(inner, dialect)?)),
        Expr::Between(between) => {
            let target = try_expr_to_sql(&between.expr, dialect)?;
            let low = try_expr_to_sql(&between.low, dialect)?;
            let high = try_expr_to_sql(&between.high, dialect)?;
            Some(if between.negated {
                format!("({} NOT BETWEEN {} AND {})", target, low, high)
            } else {
                format!("({} BETWEEN {} AND {})", target, low, high)
            })
        }
        Expr::InList(in_list) => {
            let target = try_expr_to_sql(&in_list.expr, dialect)?;
            let mut values = Vec::with_capacity(in_list.list.len());
            for e in &in_list.list {
                values.push(try_expr_to_sql(e, dialect)?);
            }
            Some(if in_list.negated {
                format!("({} NOT IN ({}))", target, values.join(", "))
            } else {
                format!("({} IN ({}))", target, values.join(", "))
            })
        }
        Expr::Wildcard { .. } => Some("*".to_string()),
        _ => {
            // ScalarFunction / Cast / Interval / etc. cannot be faithfully translated
            // to the remote dialect — skip pushdown so DataFusion applies it locally.
            debug!("Non-pushable expression type: {:?}", expr);
            None
        }
    }
}

/// Convert a `ScalarValue` to a remote SQL literal.
///
/// Returns `None` for values that cannot be represented faithfully (e.g. interval
/// literals), so the enclosing predicate is applied locally instead of being pushed
/// down as an incorrect literal (the old code emitted `NULL`, silently changing
/// semantics — notably for `Decimal128`, which is now rendered explicitly).
fn try_literal_to_sql(val: &datafusion_common::ScalarValue) -> Option<String> {
    use datafusion_common::ScalarValue;
    match val {
        ScalarValue::Utf8(Some(s)) | ScalarValue::LargeUtf8(Some(s)) => {
            Some(format!("'{}'", s.replace('\'', "''")))
        }
        ScalarValue::Utf8(None) | ScalarValue::LargeUtf8(None) => Some("NULL".to_string()),
        ScalarValue::Boolean(Some(b)) => {
            Some(if *b { "TRUE".to_string() } else { "FALSE".to_string() })
        }
        ScalarValue::Boolean(None) => Some("NULL".to_string()),
        ScalarValue::Int8(Some(n)) => Some(n.to_string()),
        ScalarValue::Int16(Some(n)) => Some(n.to_string()),
        ScalarValue::Int32(Some(n)) => Some(n.to_string()),
        ScalarValue::Int64(Some(n)) => Some(n.to_string()),
        ScalarValue::UInt8(Some(n)) => Some(n.to_string()),
        ScalarValue::UInt16(Some(n)) => Some(n.to_string()),
        ScalarValue::UInt32(Some(n)) => Some(n.to_string()),
        ScalarValue::UInt64(Some(n)) => Some(n.to_string()),
        ScalarValue::Float32(Some(f)) => Some(format!("{}", f)),
        ScalarValue::Float64(Some(f)) => Some(format!("{}", f)),
        ScalarValue::Decimal128(Some(v), _precision, scale) => Some(decimal_to_sql(*v, *scale)),
        ScalarValue::Date32(Some(d)) => {
            let date = chrono::NaiveDate::from_ymd_opt(1970, 1, 1)
                .unwrap()
                .checked_add_days(chrono::Days::new(*d as u64))
                .unwrap();
            Some(format!("'{}'", date.format("%Y-%m-%d")))
        }
        ScalarValue::Date32(None) => Some("NULL".to_string()),
        ScalarValue::Date64(Some(d)) => {
            let dt = chrono::DateTime::from_timestamp_millis(*d).unwrap();
            Some(format!("'{}'", dt.format("%Y-%m-%d %H:%M:%S")))
        }
        ScalarValue::TimestampSecond(Some(s), _) => {
            let dt = chrono::DateTime::from_timestamp(*s, 0).unwrap();
            Some(format!("'{}'", dt.format("%Y-%m-%d %H:%M:%S")))
        }
        ScalarValue::TimestampMillisecond(Some(ms), _) => {
            let dt = chrono::DateTime::from_timestamp_millis(*ms).unwrap();
            Some(format!("'{}'", dt.format("%Y-%m-%d %H:%M:%S")))
        }
        ScalarValue::TimestampMicrosecond(Some(us), _) => {
            let dt = chrono::DateTime::from_timestamp_micros(*us).unwrap();
            Some(format!("'{}'", dt.format("%Y-%m-%d %H:%M:%S%.6f")))
        }
        // Typed NULLs for the common numeric types — safe to render as NULL.
        ScalarValue::Int8(None)
        | ScalarValue::Int16(None)
        | ScalarValue::Int32(None)
        | ScalarValue::Int64(None)
        | ScalarValue::UInt8(None)
        | ScalarValue::UInt16(None)
        | ScalarValue::UInt32(None)
        | ScalarValue::UInt64(None)
        | ScalarValue::Float32(None)
        | ScalarValue::Float64(None)
        | ScalarValue::Decimal128(None, _, _) => Some("NULL".to_string()),
        _ => {
            debug!("Non-pushable scalar value: {:?}", val);
            None
        }
    }
}

/// Render an `i128` decimal (with scale) as a plain SQL numeric literal,
/// e.g. `(12345, 2)` -> `123.45`, `(-500, 2)` -> `-5.00`, `(42, 0)` -> `42`.
fn decimal_to_sql(v: i128, scale: i8) -> String {
    let scale = if scale < 0 { 0 } else { scale } as u32;
    let sign = if v < 0 { "-" } else { "" };
    let abs = v.unsigned_abs();
    if scale == 0 {
        return format!("{}{}", sign, abs);
    }
    let pow = 10u128.pow(scale);
    let int_part = abs / pow;
    let frac_part = abs % pow;
    format!("{}{}.{:0width$}", sign, int_part, frac_part, width = scale as usize)
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::datatypes::DataType;
    use datafusion::logical_expr::Cast;
    use datafusion::prelude::{col, lit};
    use datafusion_common::ScalarValue;

    #[test]
    fn test_decimal_to_sql() {
        assert_eq!(decimal_to_sql(12345, 2), "123.45");
        assert_eq!(decimal_to_sql(-500, 2), "-5.00");
        assert_eq!(decimal_to_sql(42, 0), "42");
        assert_eq!(decimal_to_sql(-42, 0), "-42");
        assert_eq!(decimal_to_sql(5, 3), "0.005");
    }

    #[test]
    fn test_decimal_literal_is_pushable() {
        // Previously Decimal128 fell through to "NULL" (silently wrong); now it renders.
        let out = try_literal_to_sql(&ScalarValue::Decimal128(Some(12345), 10, 2)).unwrap();
        assert_eq!(out, "123.45");
    }

    #[test]
    fn test_simple_comparison_is_pushed_down() {
        // `stat_date` > DATE '2026-...' -> fully translatable, pushed to remote SQL.
        let filter = col("stat_date").gt(lit(ScalarValue::Date32(Some(20500))));
        let out = try_expr_to_sql(&filter, &SqlDialect::Doris).unwrap();
        assert!(out.contains("`stat_date`"), "got: {}", out);
        assert!(out.contains('>'), "got: {}", out);
        assert!(!out.contains("unsupported"), "got: {}", out);
    }

    #[test]
    fn test_date_arithmetic_is_not_pushed_down() {
        // The bug: `stat_date >= CAST(now() - INTERVAL '7 days' AS DATE)` used to emit
        // `/* unsupported */` into remote SQL. Now any predicate containing a CAST /
        // scalar-function / interval sub-expression returns None so DataFusion applies
        // it locally instead of breaking the remote parser.
        let cast_expr = Expr::Cast(Cast::new(Box::new(col("x")), DataType::Date32));
        let filter = col("stat_date").gt_eq(cast_expr);
        assert!(
            try_expr_to_sql(&filter, &SqlDialect::Doris).is_none(),
            "date arithmetic predicate must NOT be pushed down"
        );
    }

    #[test]
    fn test_bare_cast_is_not_pushed_down() {
        let cast_expr = Expr::Cast(Cast::new(Box::new(col("x")), DataType::Date32));
        assert!(try_expr_to_sql(&cast_expr, &SqlDialect::Doris).is_none());
    }

    #[test]
    fn test_never_emits_unsupported_marker() {
        // Guard: no translatable expression should ever contain the placeholder.
        let filter = col("region")
            .eq(lit("cn"))
            .and(col("case_cnt").gt(lit(0i64)));
        let out = try_expr_to_sql(&filter, &SqlDialect::Doris).unwrap();
        assert!(!out.contains("/* unsupported */"), "got: {}", out);
        assert!(out.contains("`region`"), "got: {}", out);
    }
}

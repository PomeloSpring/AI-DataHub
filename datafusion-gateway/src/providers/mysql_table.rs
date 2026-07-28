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
use mysql_async::Pool;
use tracing::debug;

use super::remote_exec::RemoteSqlExec;

/// SQL dialect for remote database
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SqlDialect {
    MySQL,
    Doris,
}

impl SqlDialect {
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "doris" => Self::Doris,
            _ => Self::MySQL,
        }
    }

    /// Quote an identifier
    pub fn quote_identifier(&self, name: &str) -> String {
        match self {
            Self::Doris => format!("`{}`", name.replace('`', "``")),
            Self::MySQL => format!("`{}`", name.replace('`', "``")),
        }
    }
}

/// Remote MySQL/Doris table provider.
///
/// Translates DataFusion query plans into remote SQL that gets pushed down
/// to the underlying MySQL/Doris database for execution.
pub struct RemoteSqlTable {
    pool: Pool,
    database: String,
    table_name: String,
    schema: SchemaRef,
    dialect: SqlDialect,
}

impl fmt::Debug for RemoteSqlTable {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("RemoteSqlTable")
            .field("database", &self.database)
            .field("table_name", &self.table_name)
            .field("dialect", &self.dialect)
            .finish()
    }
}

impl RemoteSqlTable {
    pub fn new(
        pool: Pool,
        database: String,
        table_name: String,
        schema: SchemaRef,
        dialect: SqlDialect,
    ) -> Self {
        Self {
            pool,
            database,
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
        let columns = if let Some(proj) = projection {
            proj.iter()
                .map(|i| {
                    let field = self.schema.field(*i);
                    self.dialect.quote_identifier(field.name())
                })
                .collect::<Vec<_>>()
        } else {
            vec!["*".to_string()]
        };

        // 2. Build WHERE clause (predicate pushdown)
        let where_clause = if filters.is_empty() {
            String::new()
        } else {
            let conditions: Vec<String> = filters
                .iter()
                .map(|expr| expr_to_sql(expr, &self.dialect))
                .collect();
            format!(" WHERE {}", conditions.join(" AND "))
        };

        // 3. Build LIMIT clause
        let limit_clause = limit
            .map(|n| format!(" LIMIT {}", n))
            .unwrap_or_default();

        // 4. Assemble full SQL
        let remote_sql = format!(
            "SELECT {} FROM {}.{}{}{}",
            columns.join(", "),
            self.dialect.quote_identifier(&self.database),
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
        // All filters can be exactly pushed down to the remote database
        Ok(filters
            .iter()
            .map(|f| {
                if is_supported_filter(f) {
                    TableProviderFilterPushDown::Exact
                } else {
                    TableProviderFilterPushDown::Inexact
                }
            })
            .collect())
    }
}

/// Check if a filter expression can be pushed down to the remote database
fn is_supported_filter(expr: &Expr) -> bool {
    match expr {
        Expr::BinaryExpr(binary) => {
            matches!(
                binary.op,
                Operator::Eq
                    | Operator::NotEq
                    | Operator::Lt
                    | Operator::LtEq
                    | Operator::Gt
                    | Operator::GtEq
                    | Operator::And
                    | Operator::Or
            ) && is_supported_filter(&binary.left)
                && is_supported_filter(&binary.right)
        }
        Expr::Column(_) | Expr::Literal(_) | Expr::IsNull(_) | Expr::IsNotNull(_) => true,
        Expr::Negative(inner) => is_supported_filter(inner),
        Expr::Not(inner) => is_supported_filter(inner),
        _ => false,
    }
}

/// Convert a DataFusion Expr to a SQL string fragment for the remote database
fn expr_to_sql(expr: &Expr, dialect: &SqlDialect) -> String {
    match expr {
        Expr::Column(col) => {
            if let Some(table) = &col.relation {
                format!(
                    "{}.{}",
                    dialect.quote_identifier(&table.to_string()),
                    dialect.quote_identifier(&col.name)
                )
            } else {
                dialect.quote_identifier(&col.name)
            }
        }
        Expr::Literal(val) => literal_to_sql(val),
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
                    debug!("Unsupported operator: {:?}", binary.op);
                    "/* unsupported */"
                }
            };
            format!(
                "({} {} {})",
                expr_to_sql(&binary.left, dialect),
                op_str,
                expr_to_sql(&binary.right, dialect)
            )
        }
        Expr::IsNull(inner) => format!("({} IS NULL)", expr_to_sql(inner, dialect)),
        Expr::IsNotNull(inner) => format!("({} IS NOT NULL)", expr_to_sql(inner, dialect)),
        Expr::Negative(inner) => format!("(-{})", expr_to_sql(inner, dialect)),
        Expr::Not(inner) => format!("(NOT {})", expr_to_sql(inner, dialect)),
        Expr::Between(between) => {
            if between.negated {
                format!(
                    "({} NOT BETWEEN {} AND {})",
                    expr_to_sql(&between.expr, dialect),
                    expr_to_sql(&between.low, dialect),
                    expr_to_sql(&between.high, dialect)
                )
            } else {
                format!(
                    "({} BETWEEN {} AND {})",
                    expr_to_sql(&between.expr, dialect),
                    expr_to_sql(&between.low, dialect),
                    expr_to_sql(&between.high, dialect)
                )
            }
        }
        Expr::InList(in_list) => {
            let values: Vec<String> = in_list.list.iter().map(|e| expr_to_sql(e, dialect)).collect();
            if in_list.negated {
                format!("({} NOT IN ({}))", expr_to_sql(&in_list.expr, dialect), values.join(", "))
            } else {
                format!("({} IN ({}))", expr_to_sql(&in_list.expr, dialect), values.join(", "))
            }
        }
        Expr::Wildcard { .. } => "*".to_string(),
        _ => {
            debug!("Unsupported expression type: {:?}", expr);
            "/* unsupported */".to_string()
        }
    }
}

/// Convert a ScalarValue to a SQL literal string
fn literal_to_sql(val: &datafusion_common::ScalarValue) -> String {
    use datafusion_common::ScalarValue;
    match val {
        ScalarValue::Utf8(Some(s)) | ScalarValue::LargeUtf8(Some(s)) => {
            format!("'{}'", s.replace('\'', "''"))
        }
        ScalarValue::Utf8(None) | ScalarValue::LargeUtf8(None) => "NULL".to_string(),
        ScalarValue::Boolean(Some(b)) => if *b { "TRUE".to_string() } else { "FALSE".to_string() },
        ScalarValue::Boolean(None) => "NULL".to_string(),
        ScalarValue::Int8(Some(n)) => n.to_string(),
        ScalarValue::Int16(Some(n)) => n.to_string(),
        ScalarValue::Int32(Some(n)) => n.to_string(),
        ScalarValue::Int64(Some(n)) => n.to_string(),
        ScalarValue::UInt8(Some(n)) => n.to_string(),
        ScalarValue::UInt16(Some(n)) => n.to_string(),
        ScalarValue::UInt32(Some(n)) => n.to_string(),
        ScalarValue::UInt64(Some(n)) => n.to_string(),
        ScalarValue::Float32(Some(f)) => format!("{}", f),
        ScalarValue::Float64(Some(f)) => format!("{}", f),
        ScalarValue::Date32(Some(d)) => {
            let date = chrono::NaiveDate::from_ymd_opt(1970, 1, 1)
                .unwrap()
                .checked_add_days(chrono::Days::new(*d as u64))
                .unwrap();
            format!("'{}'", date.format("%Y-%m-%d"))
        }
        ScalarValue::Date64(Some(d)) => {
            let dt = chrono::DateTime::from_timestamp_millis(*d).unwrap();
            format!("'{}'", dt.format("%Y-%m-%d %H:%M:%S"))
        }
        ScalarValue::TimestampSecond(Some(s), _) => {
            let dt = chrono::DateTime::from_timestamp(*s, 0).unwrap();
            format!("'{}'", dt.format("%Y-%m-%d %H:%M:%S"))
        }
        ScalarValue::TimestampMillisecond(Some(ms), _) => {
            let dt = chrono::DateTime::from_timestamp_millis(*ms).unwrap();
            format!("'{}'", dt.format("%Y-%m-%d %H:%M:%S"))
        }
        _ => {
            debug!("Unsupported scalar value: {:?}", val);
            "NULL".to_string()
        }
    }
}

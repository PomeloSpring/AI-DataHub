/// Doris pushdown execution — DataFusion-style SQL parsing + RLS injection,
/// then execute directly on Doris (bypassing DataFusion's execution engine).
///
/// Rationale: DataFusion is a standard-SQL analytical engine and cannot execute
/// Doris-specific functions (window_funnel, LATERAL VIEW EXPLODE, etc.). However,
/// we still want DataFusion-grade SQL parsing and RLS enforcement. This module:
///   1. Parses SQL with sqlparser (the same parser DataFusion uses) — lenient to
///      Doris-specific functions because parsing ≠ function resolution.
///   2. Injects RLS row-filters into the AST WHERE clause (per-table policies).
///   3. Regenerates SQL and executes it directly on Doris via the MySQL pool
///      (Doris speaks the MySQL wire protocol).
///
/// This keeps RLS/permissions enforced at the gateway while letting Doris use its
/// native execution engine and proprietary functions.

use std::collections::HashMap;
use std::time::Instant;

use sqlparser::ast::{BinaryOperator, Expr, Query, Select, SetExpr, Statement, TableFactor};
use sqlparser::dialect::{Dialect, GenericDialect, MySqlDialect};
use sqlparser::parser::Parser;
use tracing::{info, warn};

use crate::engine::metadata::execute_metadata_query;
use crate::providers::ConnectionPoolManager;
use crate::types::{DatasourceConfig, QueryResponse, RLSPolicy};

/// Doris-specific function/syntax markers. If a Doris query contains any of these,
/// DataFusion cannot execute it, so we push down to Doris directly.
const DORIS_PUSHDOWN_MARKERS: &[&str] = &[
    "WINDOW_FUNNEL",
    "LATERAL VIEW",
    "EXPLODE(",
    "ARRAY_RANGE",
    "BITMAP_",
    "HLL_",
    "PERCENTILE_",
    "MURMUR_HASH",
    "GROUPING__ID",
    "COLLECT_LIST",
    "COLLECT_SET",
];

/// Decide whether a query must be pushed down to Doris (bypass DataFusion execution).
///
/// Only Doris datasources qualify, and only when the SQL uses Doris-specific
/// constructs that DataFusion's planner would reject:
///   - Doris proprietary functions (window_funnel, LATERAL VIEW EXPLODE, ...)
///   - Multi-catalog federation references (catalog.db.table 3-part names), which
///     DataFusion cannot resolve because they live outside its own catalog.
pub fn needs_pushdown(datasource: &DatasourceConfig, sql: &str) -> bool {
    if datasource.db_type.to_lowercase() != "doris" {
        return false;
    }
    let upper = sql.to_uppercase();
    if DORIS_PUSHDOWN_MARKERS.iter().any(|m| upper.contains(m)) {
        return true;
    }
    // Multi-catalog federation (3-part table names) must run on Doris
    has_federation_reference(sql)
}

/// Detect Doris multi-catalog federation: any table reference with >= 3 name parts
/// (catalog.database.table). DataFusion only resolves tables within its own catalog,
/// so 3-part external-catalog references must be executed by Doris itself.
fn has_federation_reference(sql: &str) -> bool {
    let dialect = MySqlDialect {};
    let statements = match Parser::parse_sql(&dialect, sql) {
        Ok(s) => s,
        Err(_) => return false,
    };

    let mut found = false;
    for stmt in &statements {
        if let Statement::Query(query) = stmt {
            walk_query_for_federation(query, &mut found);
        }
    }
    found
}

/// Recursively walk a Query's table factors looking for 3-part (federated) names.
/// Handles SELECT, UNION/INTERSECT/EXCEPT (SetOperation), nested queries, CTEs,
/// and derived subqueries (FROM (SELECT ...)).
fn walk_query_for_federation(query: &Query, found: &mut bool) {
    if *found {
        return;
    }
    if let Some(with) = &query.with {
        for cte in &with.cte_tables {
            walk_query_for_federation(&cte.query, found);
        }
    }
    walk_set_expr_for_federation(query.body.as_ref(), found);
}

/// Walk a SetExpr recursively — covers SELECT, UNION/INTERSECT/EXCEPT, nested Query.
fn walk_set_expr_for_federation(body: &SetExpr, found: &mut bool) {
    if *found {
        return;
    }
    match body {
        SetExpr::Select(select) => {
            for twj in &select.from {
                check_factor_federation(&twj.relation, found);
                for join in &twj.joins {
                    check_factor_federation(&join.relation, found);
                }
            }
        }
        SetExpr::SetOperation { left, right, .. } => {
            walk_set_expr_for_federation(left, found);
            walk_set_expr_for_federation(right, found);
        }
        SetExpr::Query(nested) => {
            walk_query_for_federation(nested, found);
        }
        _ => {}
    }
}

/// If a table factor's name has >= 3 parts, mark federation found.
/// Also recurse into derived subqueries so `FROM (SELECT ... FROM cat.db.t)` is detected.
fn check_factor_federation(factor: &TableFactor, found: &mut bool) {
    if *found {
        return;
    }
    match factor {
        TableFactor::Table { name, .. } => {
            if name.0.len() >= 3 {
                *found = true;
            }
        }
        TableFactor::Derived { subquery, .. } => {
            walk_query_for_federation(subquery, found);
        }
        _ => {}
    }
}

/// Parse SQL, inject RLS row-filters into WHERE clauses, and regenerate SQL.
/// Uses the MySQL dialect (Doris / MySQL pushdown).
///
/// Returns `(final_sql, rls_applied_notes)`.
pub fn inject_rls_filters(
    sql: &str,
    rls_policies: &[RLSPolicy],
) -> Result<(String, Vec<String>), String> {
    inject_rls_filters_with(sql, rls_policies, &MySqlDialect {})
}

/// Parse SQL, inject RLS row-filters into WHERE clauses, and regenerate SQL
/// using the given SQL dialect.
///
/// Returns `(final_sql, rls_applied_notes)`.
pub fn inject_rls_filters_with(
    sql: &str,
    rls_policies: &[RLSPolicy],
    dialect: &dyn Dialect,
) -> Result<(String, Vec<String>), String> {
    // Build table -> row_filter lookup
    let mut policy_map: HashMap<String, &str> = HashMap::new();
    for policy in rls_policies {
        if policy.row_filter.is_empty() {
            continue;
        }
        for table in &policy.tables {
            policy_map.insert(table.to_lowercase(), policy.row_filter.as_str());
        }
    }

    // No applicable policies — return SQL unchanged
    if policy_map.is_empty() {
        return Ok((sql.to_string(), vec![]));
    }

    let mut statements =
        Parser::parse_sql(dialect, sql).map_err(|e| format!("SQL parse failed: {}", e))?;

    let mut applied: Vec<String> = Vec::new();

    for stmt in statements.iter_mut() {
        if let Statement::Query(query) = stmt {
            inject_into_query(query, &policy_map, dialect, &mut applied);
        }
    }

    let final_sql = statements
        .iter()
        .map(|s| s.to_string())
        .collect::<Vec<_>>()
        .join("; ");

    Ok((final_sql, applied))
}

/// Recursively inject RLS into a Query (handles CTEs + outer select + UNION/derived subqueries).
fn inject_into_query(
    query: &mut Query,
    policy_map: &HashMap<String, &str>,
    dialect: &dyn Dialect,
    applied: &mut Vec<String>,
) {
    // Recurse into CTEs first (base-table scans usually live here)
    if let Some(with) = query.with.as_mut() {
        for cte in with.cte_tables.iter_mut() {
            inject_into_query(&mut cte.query, policy_map, dialect, applied);
        }
    }

    inject_into_set_expr(query.body.as_mut(), policy_map, dialect, applied);
}

/// Recurse into a SetExpr: SELECT, UNION/INTERSECT/EXCEPT branches, nested Query.
fn inject_into_set_expr(
    body: &mut SetExpr,
    policy_map: &HashMap<String, &str>,
    dialect: &dyn Dialect,
    applied: &mut Vec<String>,
) {
    match body {
        SetExpr::Select(select) => {
            inject_into_select(select, policy_map, dialect, applied);
        }
        SetExpr::SetOperation { left, right, .. } => {
            inject_into_set_expr(left, policy_map, dialect, applied);
            inject_into_set_expr(right, policy_map, dialect, applied);
        }
        SetExpr::Query(nested) => {
            inject_into_query(nested, policy_map, dialect, applied);
        }
        _ => {}
    }
}

/// Inject RLS row-filters into a single SELECT's WHERE clause.
/// Also recurses into derived subqueries in FROM.
fn inject_into_select(
    select: &mut Select,
    policy_map: &HashMap<String, &str>,
    dialect: &dyn Dialect,
    applied: &mut Vec<String>,
) {
    // Recurse into derived subqueries so inner scans also get RLS
    for table_with_joins in select.from.iter_mut() {
        inject_into_factor(&mut table_with_joins.relation, policy_map, dialect, applied);
        for join in table_with_joins.joins.iter_mut() {
            inject_into_factor(&mut join.relation, policy_map, dialect, applied);
        }
    }

    // Collect RLS expressions for every base-table referenced in FROM / JOIN
    let mut rls_exprs: Vec<Expr> = Vec::new();
    for table_with_joins in &select.from {
        collect_table_filter(
            &table_with_joins.relation,
            policy_map,
            dialect,
            &mut rls_exprs,
            applied,
        );
        for join in &table_with_joins.joins {
            collect_table_filter(&join.relation, policy_map, dialect, &mut rls_exprs, applied);
        }
    }

    if rls_exprs.is_empty() {
        return;
    }

    // AND-combine all RLS expressions
    let combined = rls_exprs
        .into_iter()
        .reduce(|acc, e| Expr::BinaryOp {
            left: Box::new(acc),
            op: BinaryOperator::And,
            right: Box::new(e),
        })
        .unwrap();

    // Merge with existing WHERE (if any)
    select.selection = match select.selection.take() {
        Some(existing) => Some(Expr::BinaryOp {
            left: Box::new(existing),
            op: BinaryOperator::And,
            right: Box::new(combined),
        }),
        None => Some(combined),
    };
}

/// If the factor is a derived subquery, recurse into it.
fn inject_into_factor(
    factor: &mut TableFactor,
    policy_map: &HashMap<String, &str>,
    dialect: &dyn Dialect,
    applied: &mut Vec<String>,
) {
    if let TableFactor::Derived { subquery, .. } = factor {
        inject_into_query(subquery, policy_map, dialect, applied);
    }
}

/// If the table factor references a protected table, parse its row_filter into an Expr.
fn collect_table_filter(
    factor: &TableFactor,
    policy_map: &HashMap<String, &str>,
    dialect: &dyn Dialect,
    out: &mut Vec<Expr>,
    applied: &mut Vec<String>,
) {
    if let TableFactor::Table { name, .. } = factor {
        let qualified = name.to_string().to_lowercase();
        // Strip qualifier (db.table / catalog.db.table) -> base table name
        let base = qualified
            .rsplit('.')
            .next()
            .unwrap_or(&qualified)
            .to_string();

        if let Some(filter) = policy_map.get(&base) {
            match parse_expr(filter, dialect) {
                Ok(expr) => {
                    out.push(expr);
                    applied.push(format!("行级过滤 [{}]: {}", base, filter));
                }
                Err(e) => {
                    applied.push(format!("行级过滤解析失败 [{}]: {} ({})", base, filter, e));
                }
            }
        }
    }
}

/// Parse a WHERE-fragment string into a sqlparser Expr.
fn parse_expr(fragment: &str, dialect: &dyn Dialect) -> Result<Expr, String> {
    let mut parser = Parser::new(dialect)
        .try_with_sql(fragment)
        .map_err(|e| e.to_string())?;
    parser.parse_expr().map_err(|e| e.to_string())
}

/// Full pushdown pipeline: inject RLS then execute directly on Doris.
pub async fn execute_pushdown_query(
    pool_manager: &ConnectionPoolManager,
    datasource: &DatasourceConfig,
    sql: &str,
    rls_policies: &[RLSPolicy],
) -> QueryResponse {
    let start = Instant::now();

    let (final_sql, applied) = match inject_rls_filters(sql, rls_policies) {
        Ok(result) => result,
        Err(e) => {
            let elapsed = start.elapsed().as_millis() as u64;
            return QueryResponse::error(&format!("RLS injection failed: {}", e), elapsed);
        }
    };

    info!(
        "Doris pushdown: RLS injected ({} policies), executing directly",
        applied.len()
    );

    // Doris speaks MySQL protocol -> reuse the MySQL-pool executor
    let mut response =
        execute_metadata_query(pool_manager, datasource, &final_sql).await;

    // Surface which RLS policies were applied
    response.rls_applied = applied;
    response
}

/// Passthrough execution WITH best-effort RLS injection (for SLS etc.).
///
/// SLS bypasses DataFusion entirely (its own SQL engine), so RLS was previously
/// not enforced. This injects RLS row-filters at the AST level before executing.
///
/// Best-effort: if the SQL cannot be parsed (e.g. contains `{{param}}` placeholders
/// that are substituted later, or SLS-specific syntax), we log a warning and execute
/// the raw SQL unchanged to preserve backward compatibility.
pub async fn execute_passthrough_with_rls(
    pool_manager: &ConnectionPoolManager,
    datasource: &DatasourceConfig,
    sql: &str,
    rls_policies: &[RLSPolicy],
) -> QueryResponse {
    // SLS SQL is closest to generic/standard SQL
    let dialect = GenericDialect {};

    let (final_sql, applied) = match inject_rls_filters_with(sql, rls_policies, &dialect) {
        Ok(result) => result,
        Err(e) => {
            // Parse failed — fall back to raw SQL (fail-open, same as legacy behavior).
            // Log prominently so operators can notice un-injected RLS.
            warn!(
                "Passthrough RLS injection skipped (parse failed): {} — executing raw SQL",
                e
            );
            (sql.to_string(), vec![format!("RLS 注入跳过(解析失败): {}", e)])
        }
    };

    if applied.iter().any(|a| a.contains("行级过滤")) {
        info!("Passthrough ({}): RLS injected, executing", datasource.db_type);
    }

    let mut response =
        execute_metadata_query(pool_manager, datasource, &final_sql).await;
    response.rls_applied = applied;
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::RLSPolicy;
    use std::collections::HashMap;

    fn policy(table: &str, filter: &str) -> RLSPolicy {
        RLSPolicy {
            tables: vec![table.to_string()],
            row_filter: filter.to_string(),
            hidden_columns: vec![],
            masked_columns: HashMap::new(),
        }
    }

    #[test]
    fn test_needs_pushdown_doris_funnel() {
        let ds = DatasourceConfig {
            db_type: "doris".into(),
            host: "h".into(),
            port: 9030,
            database: "db".into(),
            user: "u".into(),
            password: "p".into(),
            ssl: None,
            ssl_mode: None,
        };
        let sql = "SELECT window_funnel(1800, 'default', ts, a=1) FROM t";
        assert!(needs_pushdown(&ds, sql));

        // Standard SQL on doris -> no pushdown (stays in DataFusion)
        let std_sql = "SELECT COUNT(*) FROM t WHERE x = 1";
        assert!(!needs_pushdown(&ds, std_sql));

        // Non-doris datasource -> no pushdown
        let mysql = DatasourceConfig { db_type: "mysql".into(), ..ds.clone() };
        assert!(!needs_pushdown(&mysql, sql));
    }

    #[test]
    fn test_needs_pushdown_doris_federation() {
        let ds = DatasourceConfig {
            db_type: "doris".into(),
            host: "h".into(),
            port: 9030,
            database: "db".into(),
            user: "u".into(),
            password: "p".into(),
            ssl: None,
            ssl_mode: None,
        };
        // 3-part federated name -> pushdown
        let fed = "SELECT a.region, SUM(b.revenue) FROM doris_dw.dwd_sales a JOIN catalog_mysql.bizdb.t_orders b ON a.id=b.id GROUP BY a.region";
        assert!(needs_pushdown(&ds, fed), "federation should pushdown");

        // 2-part local name -> no pushdown (standard SQL stays in DataFusion)
        let local = "SELECT COUNT(*) FROM mydb.t_orders";
        assert!(!needs_pushdown(&ds, local));

        // 1-part local name -> no pushdown
        let one = "SELECT COUNT(*) FROM t_orders";
        assert!(!needs_pushdown(&ds, one));
    }

    #[test]
    fn test_inject_rls_simple_select() {
        let sql = "SELECT pcid, COUNT(*) AS c FROM dwd_rum GROUP BY pcid";
        let (out, applied) = inject_rls_filters(sql, &[policy("dwd_rum", "workspace_id = 'ws1'")]).unwrap();
        assert_eq!(applied.len(), 1);
        let upper = out.to_uppercase();
        assert!(upper.contains("WHERE"), "should add WHERE: {}", out);
        assert!(upper.contains("WORKSPACE_ID"), "should contain RLS col: {}", out);
    }

    #[test]
    fn test_inject_rls_with_existing_where() {
        let sql = "SELECT * FROM dwd_rum WHERE del_flag = 0";
        let (out, _) = inject_rls_filters(sql, &[policy("dwd_rum", "workspace_id = 'ws1'")]).unwrap();
        let upper = out.to_uppercase();
        assert!(upper.contains("DEL_FLAG = 0"), "keep original: {}", out);
        assert!(upper.contains("WORKSPACE_ID"), "add RLS: {}", out);
        assert!(upper.contains(" AND "), "AND-combined: {}", out);
    }

    #[test]
    fn test_inject_rls_cte_funnel() {
        // The real funnel shape: WITH ... SELECT from base table inside CTE
        let sql = "WITH src AS (SELECT pcid, ts FROM dwd_rum WHERE dt > '2026-01-01') \
                   SELECT pcid FROM src";
        let (out, applied) = inject_rls_filters(sql, &[policy("dwd_rum", "workspace_id = 'ws1'")]).unwrap();
        assert_eq!(applied.len(), 1, "should apply RLS inside CTE");
        let upper = out.to_uppercase();
        assert!(upper.contains("WORKSPACE_ID"), "RLS injected into CTE: {}", out);
    }

    #[test]
    fn test_inject_rls_no_policy_unchanged() {
        let sql = "SELECT * FROM other_table";
        let (out, applied) = inject_rls_filters(sql, &[policy("dwd_rum", "workspace_id = 'ws1'")]).unwrap();
        assert!(applied.is_empty());
        assert!(!out.to_uppercase().contains("WORKSPACE_ID"));
    }

    #[test]
    fn test_inject_rls_sls_generic_dialect() {
        // SLS-style SQL with __time__ field, parsed with GenericDialect
        let sql = "SELECT COUNT(DISTINCT usercode) AS active_users FROM observability_rum WHERE __time__ >= 100 AND __time__ <= 200";
        let (out, applied) = inject_rls_filters_with(
            sql,
            &[policy("observability_rum", "workspace_id = 'ws1'")],
            &GenericDialect {},
        ).unwrap();
        assert_eq!(applied.len(), 1);
        let upper = out.to_uppercase();
        assert!(upper.contains("__TIME__ >= 100"), "keep original: {}", out);
        assert!(upper.contains("WORKSPACE_ID"), "add RLS: {}", out);
    }
}

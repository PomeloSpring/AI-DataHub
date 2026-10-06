use std::collections::HashMap;
use std::sync::Arc;

use datafusion::common::Result;
use datafusion::execution::session_state::SessionStateBuilder;
use datafusion::prelude::SessionContext;
use tracing::info;

use crate::providers::{ConnectionPoolManager, RemoteSqlTable, mysql_table::SqlDialect};
use crate::schema::discovery::{self, table_to_arrow_schema};
use crate::security::{RLSAnalyzerRule, RLSVerifierRule, SecureCatalog, SecureTableProvider, DEFAULT_CATALOG};
use crate::types::{DatasourceConfig, FederatedSource, RLSPolicy};

type TableProviderMap = HashMap<String, Arc<dyn datafusion::datasource::TableProvider>>;

/// Query session — creates a per-request DataFusion context with RLS-secured tables.
pub struct QuerySession;

impl QuerySession {
    /// Create a new DataFusion session with RLS-secured tables.
    ///
    /// Steps (per source):
    /// 1. Get/create connection pool for the datasource
    /// 2. Discover table schemas from INFORMATION_SCHEMA
    /// 3. Create RemoteSqlTable for each table
    /// 4. Wrap tables with SecureTableProvider based on RLS policies
    /// 5. Register SecureCatalog (按 qualifier 分组 schema)
    ///
    /// 跨源联邦（federated）：主源注册 DEFAULT_CATALOG（裸名/双段限定名解析），
    /// 每个联邦源注册独立 catalog（catalog 名 = 数据源名），SQL 中以
    /// `ds.db.table` 三段式限定名引用；RLS 策略按限定名匹配键逐源施加。
    pub async fn create(
        pool_manager: &ConnectionPoolManager,
        datasource: &DatasourceConfig,
        rls_policies: &[RLSPolicy],
        federated: &[FederatedSource],
    ) -> Result<(SessionContext, Vec<String>)> {
        let mut rls_applied = Vec::new();

        // Build policy lookup: 匹配键(SQL 写法, 小写) -> policy
        let mut policy_map: HashMap<String, &RLSPolicy> = HashMap::new();
        for policy in rls_policies {
            for table in &policy.tables {
                policy_map.insert(table.to_lowercase(), policy);
            }
        }

        // Pre-parsed RLS filter expressions for RLSAnalyzerRule (logical plan level)
        let mut rls_filter_exprs: HashMap<String, datafusion::logical_expr::Expr> = HashMap::new();

        // 主源 → DEFAULT_CATALOG（裸表名 `t` 与双段 `db.t` 解析）
        let (main_groups, applied) = build_source_tables(
            pool_manager, datasource, "", false, &policy_map, &mut rls_filter_exprs,
        ).await?;
        rls_applied.extend(applied);

        // 联邦源 → 各自 catalog（三段式 `ds.db.t` 解析）
        let mut federated_groups = Vec::new();
        for source in federated {
            let (groups, applied) = build_source_tables(
                pool_manager, &source.config, &source.name, true, &policy_map, &mut rls_filter_exprs,
            ).await?;
            rls_applied.extend(applied);
            federated_groups.push((source.name.clone(), groups));
        }

        info!(
            "Created session: main sources + {} federated catalogs, {} RLS policies applied",
            federated_groups.len(),
            rls_applied.len()
        );

        // Create session with secure catalogs and custom RLS rules
        //    Custom rules provide defense-in-depth:
        //    - RLSAnalyzerRule: injects Filter nodes at logical plan level (before optimization)
        //    - RLSVerifierRule: verifies RLS filters survive optimization (after optimization)
        //    - SecureTableProvider: enforces RLS at physical scan level (already done above)
        let base_ctx = SessionContext::new();
        let state = SessionStateBuilder::new_from_existing(base_ctx.state().clone())
            .with_analyzer_rule(Arc::new(RLSAnalyzerRule::new(rls_filter_exprs)))
            .with_optimizer_rule(Arc::new(RLSVerifierRule::from_policies(rls_policies)))
            .build();
        let ctx = SessionContext::new_with_state(state);
        // 引擎级 MySQL 兼容函数（INET_ATON/INET_NTOA 等）：
        // lookup 型 UDF 展开模板会引用，保证 SQL 下发 DataEngine 即可执行
        for udf in crate::engine::net_funcs::net_udfs() {
            ctx.register_udf(udf);
        }
        ctx.register_catalog(DEFAULT_CATALOG, Arc::new(SecureCatalog::from_groups(main_groups)));
        for (name, groups) in federated_groups {
            ctx.register_catalog(name.as_str(), Arc::new(SecureCatalog::from_groups(groups)));
        }

        Ok((ctx, rls_applied))
    }
}

/// 为单个数据源构建 SecureCatalog 分组（qualifier -> tables）。
///
/// `source_name`：联邦源的 catalog 名（数据源名）；主源传空串。
/// `is_federated`：决定 RLS 策略匹配键变体（联邦源只认 `ds.db.table` 三段式，
/// 主源认 `db.table`/裸名），与 Python 侧 enforcer._resolve_table_ref 语义一致。
async fn build_source_tables(
    pool_manager: &ConnectionPoolManager,
    datasource: &DatasourceConfig,
    source_name: &str,
    is_federated: bool,
    policy_map: &HashMap<String, &RLSPolicy>,
    rls_filter_exprs: &mut HashMap<String, datafusion::logical_expr::Expr>,
) -> Result<(HashMap<String, TableProviderMap>, Vec<String>)> {
    let mut rls_applied = Vec::new();

    // 1. Get connection pool
    let pool = pool_manager.get_or_create(datasource).await;
    let dialect = SqlDialect::from_str(&datasource.db_type);

    // 2. Discover table schemas
    let discovered_tables = discovery::SchemaDiscovery::discover_all(&pool, &datasource.database, dialect)
        .await
        .map_err(|e| {
            datafusion::common::DataFusionError::Execution(format!(
                "Schema discovery failed: {}",
                e
            ))
        })?;

    // 3. Create table providers, grouped by qualifier (database / namespace)
    let mut groups: HashMap<String, TableProviderMap> = HashMap::new();

    for discovered in &discovered_tables {
        let schema = table_to_arrow_schema(discovered, dialect);

        // Base remote table — qualifier is database (MySQL/Doris) or schema (Postgres)
        let base_table = Arc::new(RemoteSqlTable::new(
            pool.clone(),
            discovered.qualifier.clone(),
            discovered.name.clone(),
            schema.clone(),
            dialect,
        ));

        // RLS 策略匹配键变体（与 SQL 写法对齐；跨库同名不串味）
        let table_lower = discovered.name.to_lowercase();
        let qualifier_lower = discovered.qualifier.to_lowercase();
        let variants: Vec<String> = if is_federated {
            vec![format!("{}.{}.{}", source_name.to_lowercase(), qualifier_lower, table_lower)]
        } else {
            vec![format!("{}.{}", qualifier_lower, table_lower), table_lower.clone()]
        };
        let policy = variants.iter().find_map(|key| policy_map.get(key.as_str()).copied());

        let provider: Arc<dyn datafusion::datasource::TableProvider> = if let Some(policy) = policy {
            let matched_key = variants
                .iter()
                .find(|key| policy_map.contains_key(key.as_str()))
                .cloned()
                .unwrap_or_else(|| table_lower.clone());

            // Parse row filter expression
            let row_filter = if !policy.row_filter.is_empty() {
                match parse_filter_expr(&policy.row_filter, &schema).await {
                    Ok(expr) => {
                        rls_applied.push(format!("行级过滤 [{}]: {}", matched_key, policy.row_filter));
                        // Also store for RLSAnalyzerRule (logical plan level defense)
                        rls_filter_exprs.insert(matched_key.clone(), expr.clone());
                        Some(expr)
                    }
                    Err(e) => {
                        // 行级过滤解析失败 = 拒绝执行（fail-closed，不静默放行）
                        return Err(datafusion::common::DataFusionError::Execution(format!(
                            "RLS 行级过滤解析失败 [{}]: {} ({})",
                            matched_key, policy.row_filter, e
                        )));
                    }
                }
            } else {
                None
            };

            // Hidden columns
            let hidden = if !policy.hidden_columns.is_empty() {
                rls_applied.push(format!(
                    "隐藏列 [{}]: {}",
                    matched_key,
                    policy.hidden_columns.join(", ")
                ));
                policy.hidden_columns.clone()
            } else {
                vec![]
            };

            // Masked columns
            let masked = if !policy.masked_columns.is_empty() {
                rls_applied.push(format!(
                    "脱敏列 [{}]: {}",
                    matched_key,
                    policy.masked_columns.keys().cloned().collect::<Vec<_>>().join(", ")
                ));
                policy.masked_columns.clone()
            } else {
                HashMap::new()
            };

            // Wrap with secure provider
            Arc::new(SecureTableProvider::new(
                base_table as Arc<dyn datafusion::datasource::TableProvider>,
                row_filter,
                hidden,
                masked,
            )) as Arc<dyn datafusion::datasource::TableProvider>
        } else {
            // No RLS policy — use base table directly
            base_table as Arc<dyn datafusion::datasource::TableProvider>
        };

        groups
            .entry(discovered.qualifier.clone())
            .or_default()
            .insert(discovered.name.clone(), provider);
    }

    Ok((groups, rls_applied))
}

/// Parse a filter expression string into a DataFusion Expr
async fn parse_filter_expr(expr_str: &str, schema: &arrow::datatypes::SchemaRef) -> Result<datafusion::logical_expr::Expr> {
    use arrow::record_batch::RecordBatch;
    use datafusion::datasource::MemTable;

    // Create a temporary context to parse the expression
    let ctx = SessionContext::new();

    // Register a dummy table with the schema to provide context
    let empty_batch = RecordBatch::new_empty(schema.clone());
    let provider = MemTable::try_new(schema.clone(), vec![vec![empty_batch]])?;
    ctx.register_table("_temp", Arc::new(provider))?;

    // Parse the filter as a WHERE clause using SQL
    let sql = format!("SELECT * FROM _temp WHERE {}", expr_str);

    let df = ctx.sql(&sql).await?;
    let plan = df.logical_plan().clone();

    // Extract the filter predicate from the plan (may be nested under Projection etc.)
    if let Some(predicate) = find_filter_predicate(&plan) {
        Ok(predicate)
    } else {
        Err(datafusion::common::DataFusionError::Plan(format!(
            "Failed to parse filter expression: {}",
            expr_str
        )))
    }
}

/// Recursively search the plan tree for a Filter node's predicate
fn find_filter_predicate(plan: &datafusion::logical_expr::LogicalPlan) -> Option<datafusion::logical_expr::Expr> {
    if let datafusion::logical_expr::LogicalPlan::Filter(filter) = plan {
        return Some(filter.predicate.clone());
    }
    for input in plan.inputs() {
        if let Some(pred) = find_filter_predicate(input) {
            return Some(pred);
        }
    }
    None
}

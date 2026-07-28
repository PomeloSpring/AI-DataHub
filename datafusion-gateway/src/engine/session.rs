use std::collections::HashMap;
use std::sync::Arc;

use datafusion::common::Result;
use datafusion::prelude::SessionContext;
use tracing::info;

use crate::providers::{ConnectionPoolManager, RemoteSqlTable, mysql_table::SqlDialect};
use crate::schema::discovery::{self, table_to_arrow_schema};
use crate::security::SecureCatalog;
use crate::security::SecureTableProvider;
use crate::types::{DatasourceConfig, RLSPolicy};

/// Query session — creates a per-request DataFusion context with RLS-secured tables.
pub struct QuerySession;

impl QuerySession {
    /// Create a new DataFusion session with RLS-secured tables.
    ///
    /// Steps:
    /// 1. Get/create connection pool for the datasource
    /// 2. Discover table schemas from INFORMATION_SCHEMA
    /// 3. Create RemoteSqlTable for each table
    /// 4. Wrap tables with SecureTableProvider based on RLS policies
    /// 5. Register SecureCatalog into SessionContext
    pub async fn create(
        pool_manager: &ConnectionPoolManager,
        datasource: &DatasourceConfig,
        rls_policies: &[RLSPolicy],
    ) -> Result<(SessionContext, Vec<String>)> {
        let mut rls_applied = Vec::new();

        // 1. Get connection pool
        let pool = pool_manager.get_or_create(datasource).await;
        let dialect = SqlDialect::from_str(&datasource.db_type);

        // 2. Discover table schemas
        let discovered_tables = discovery::SchemaDiscovery::discover_all(&pool, &datasource.database)
            .await
            .map_err(|e| {
                datafusion::common::DataFusionError::Execution(format!(
                    "Schema discovery failed: {}",
                    e
                ))
            })?;

        // 3. Build policy lookup: table_name -> policy
        let mut policy_map: HashMap<String, &RLSPolicy> = HashMap::new();
        for policy in rls_policies {
            for table in &policy.tables {
                policy_map.insert(table.to_lowercase(), policy);
            }
        }

        // 4. Create table providers
        let mut tables: HashMap<String, Arc<dyn datafusion::datasource::TableProvider>> =
            HashMap::new();

        for discovered in &discovered_tables {
            let schema = table_to_arrow_schema(discovered);

            // Base remote table
            let base_table = Arc::new(RemoteSqlTable::new(
                pool.clone(),
                datasource.database.clone(),
                discovered.name.clone(),
                schema.clone(),
                dialect,
            ));

            // Check if RLS policy exists for this table
            let table_lower = discovered.name.to_lowercase();
            if let Some(policy) = policy_map.get(&table_lower) {
                // Parse row filter expression
                let row_filter = if !policy.row_filter.is_empty() {
                    match parse_filter_expr(&policy.row_filter, &schema) {
                        Ok(expr) => {
                            rls_applied.push(format!("行级过滤 [{}]: {}", discovered.name, policy.row_filter));
                            Some(expr)
                        }
                        Err(e) => {
                            rls_applied.push(format!(
                                "行级过滤解析失败 [{}]: {} ({})",
                                discovered.name, policy.row_filter, e
                            ));
                            None
                        }
                    }
                } else {
                    None
                };

                // Hidden columns
                let hidden = if !policy.hidden_columns.is_empty() {
                    rls_applied.push(format!(
                        "隐藏列 [{}]: {}",
                        discovered.name,
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
                        discovered.name,
                        policy.masked_columns.keys().cloned().collect::<Vec<_>>().join(", ")
                    ));
                    policy.masked_columns.clone()
                } else {
                    HashMap::new()
                };

                // Wrap with secure provider
                let secure_table = Arc::new(SecureTableProvider::new(
                    base_table as Arc<dyn datafusion::datasource::TableProvider>,
                    row_filter,
                    hidden,
                    masked,
                ));

                tables.insert(
                    discovered.name.clone(),
                    secure_table as Arc<dyn datafusion::datasource::TableProvider>,
                );
            } else {
                // No RLS policy — use base table directly
                tables.insert(
                    discovered.name.clone(),
                    base_table as Arc<dyn datafusion::datasource::TableProvider>,
                );
            }
        }

        info!(
            "Created session with {} tables, {} RLS policies applied",
            tables.len(),
            rls_applied.len()
        );

        // 5. Create session with secure catalog
        //    Register as "datafusion" — DataFusion's DEFAULT_CATALOG,
        //    so unqualified table names (e.g. SELECT * FROM t) resolve correctly.
        let ctx = SessionContext::new();
        let catalog = SecureCatalog::new(tables);
        ctx.register_catalog("datafusion", Arc::new(catalog));

        Ok((ctx, rls_applied))
    }
}

/// Parse a filter expression string into a DataFusion Expr
fn parse_filter_expr(expr_str: &str, schema: &arrow::datatypes::SchemaRef) -> Result<datafusion::logical_expr::Expr> {
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

    // Use a blocking approach within the async context
    let df = tokio::runtime::Handle::current().block_on(ctx.sql(&sql))?;
    let plan = df.logical_plan().clone();

    // Extract the filter predicate from the plan
    if let datafusion::logical_expr::LogicalPlan::Filter(filter) = plan {
        Ok(filter.predicate.clone())
    } else {
        Err(datafusion::common::DataFusionError::Plan(format!(
            "Failed to parse filter expression: {}",
            expr_str
        )))
    }
}

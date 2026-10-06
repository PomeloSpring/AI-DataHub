//! Plan-level RLS defense-in-depth rules.
//!
//! These rules provide two additional layers of RLS enforcement on top of
//! the physical-layer `SecureTableProvider`:
//!
//! 1. `RLSAnalyzerRule` — runs during logical plan analysis (before optimization).
//!    Injects `Filter` nodes above `TableScan` for tables with RLS policies.
//!    This ensures the logical plan explicitly contains RLS predicates, enabling
//!    the optimizer to reason about them (e.g., push them down to the source).
//!
//! 2. `RLSVerifierRule` — runs after optimization.
//!    Verifies that RLS filters were not incorrectly eliminated by the optimizer.
//!    If a `Filter` node is missing AND the table is not a `SecureTableProvider`,
//!    the query is rejected.
//!
//! Defense-in-depth layers:
//!   Layer 1: Python/sqlglot RLS injection (before engine)
//!   Layer 2: RLSAnalyzerRule (logical plan, this module)
//!   Layer 3: RLSVerifierRule (post-optimization verification, this module)
//!   Layer 4: SecureTableProvider (physical scan, secure_table.rs)

use std::collections::HashMap;
use std::sync::Arc;

use datafusion::common::config::ConfigOptions;
use datafusion::common::tree_node::{Transformed, TreeNode, TreeNodeRecursion};
use datafusion::common::{DataFusionError, Result};
use datafusion::logical_expr::{Expr, Filter, LogicalPlan, TableScan};
use datafusion::optimizer::analyzer::AnalyzerRule;
use datafusion::optimizer::{OptimizerConfig, OptimizerRule};
use tracing::debug;

// ─── AnalyzerRule: inject RLS filters at logical plan level ────────────

/// Injects RLS row filters as `Filter` nodes above `TableScan` in the logical plan.
///
/// This runs before optimization, giving the optimizer a chance to push the
/// injected filters down to the data source for better performance.
/// The `SecureTableProvider` still enforces RLS at the physical scan level
/// as a safety net, so even if the optimizer removes a `Filter` node,
/// data access is still controlled.
pub struct RLSAnalyzerRule {
    /// Pre-parsed row filter expressions per table: table_name -> Expr
    /// Parsed in async context (session.rs) before being passed here.
    filter_exprs: HashMap<String, Expr>,
}

impl RLSAnalyzerRule {
    /// Create a new RLSAnalyzerRule with pre-parsed filter expressions.
    ///
    /// The `filter_exprs` map should be populated in an async context
    /// (e.g., `QuerySession::create`) using `parse_filter_expr`.
    pub fn new(filter_exprs: HashMap<String, Expr>) -> Self {
        Self { filter_exprs }
    }

    /// Walk the plan bottom-up and inject Filter nodes above TableScans.
    fn inject_filters(&self, plan: LogicalPlan) -> Result<LogicalPlan> {
        plan.transform(|node| {
            match node {
                LogicalPlan::TableScan(scan) => {
                    // 匹配键变体查找（裸名/db.table/ds.db.table），跨库同名不串味
                    let hit = crate::security::ref_key_variants(
                        &scan.table_name, crate::security::DEFAULT_CATALOG,
                    )
                    .iter()
                    .find_map(|key| self.filter_exprs.get(key).map(|e| (key.clone(), e.clone())));
                    if let Some((table_key, filter_expr)) = hit {
                        debug!(
                            "RLSAnalyzerRule: injecting filter for table '{}': {}",
                            table_key, filter_expr
                        );
                        let filter_plan = LogicalPlan::Filter(Filter::try_new(
                            filter_expr,
                            Arc::new(LogicalPlan::TableScan(scan)),
                        )?);
                        Ok(Transformed::yes(filter_plan))
                    } else {
                        // No RLS policy for this table — reconstruct TableScan
                        Ok(Transformed::no(LogicalPlan::TableScan(scan)))
                    }
                }
                other => Ok(Transformed::no(other)),
            }
        })
        .map(|t| t.data)
    }
}

impl AnalyzerRule for RLSAnalyzerRule {
    fn analyze(&self, plan: LogicalPlan, _config: &ConfigOptions) -> Result<LogicalPlan> {
        self.inject_filters(plan)
    }

    fn name(&self) -> &str {
        "RLSAnalyzerRule"
    }
}

// ─── OptimizerRule: verify RLS filters survive optimization ────────────

/// Verifies that RLS-protected tables still have their filters enforced
/// after the optimizer has run.
///
/// For each table with an RLS policy, checks that either:
/// - A `Filter` node exists directly above the `TableScan` (logical enforcement), OR
/// - The `TableScan` source is a `SecureTableProvider` (physical enforcement)
///
/// If neither condition holds, the query is rejected — this indicates the
/// optimizer incorrectly eliminated the RLS filter injected by `RLSAnalyzerRule`.
pub struct RLSVerifierRule {
    /// Table names that have RLS row filters
    rls_tables: Vec<String>,
}

impl RLSVerifierRule {
    /// Create a new RLSVerifierRule from RLS policies.
    pub fn from_policies(policies: &[crate::types::RLSPolicy]) -> Self {
        let mut rls_tables = Vec::new();
        for policy in policies {
            if !policy.row_filter.is_empty() {
                for table in &policy.tables {
                    let lower = table.to_lowercase();
                    if !rls_tables.contains(&lower) {
                        rls_tables.push(lower);
                    }
                }
            }
        }
        Self { rls_tables }
    }

    /// Check if a TableScan's source is a SecureTableProvider
    fn is_secure_table(scan: &TableScan) -> bool {
        scan.source
            .as_any()
            .downcast_ref::<crate::security::SecureTableProvider>()
            .is_some()
    }

    /// Walk the plan and verify RLS enforcement for all protected tables
    fn verify_rls(&self, plan: &LogicalPlan) -> Result<()> {
        // Log RLS filters that are directly above TableScans
        plan.apply(|node| {
            if let LogicalPlan::Filter(filter) = node {
                if let LogicalPlan::TableScan(scan) = filter.input.as_ref() {
                    if self.scan_matches_rls(scan) {
                        debug!(
                            "RLSVerifierRule: verified RLS filter on table '{}': {}",
                            scan.table_name, filter.predicate
                        );
                    }
                }
            }
            Ok(TreeNodeRecursion::Continue)
        })?;

        // Verify every RLS-protected table has a Filter or SecureTableProvider
        self.verify_filters_present(plan)
    }

    /// For each RLS-protected table, verify that either:
    /// - A Filter node exists above its TableScan, OR
    /// - The TableScan source is a SecureTableProvider
    fn verify_filters_present(&self, plan: &LogicalPlan) -> Result<()> {
        for rls_table in &self.rls_tables {
            let has_filter = self.table_has_filter_above(plan, rls_table);
            let has_secure = self.table_is_secure(plan, rls_table);

            if !has_filter && !has_secure {
                return Err(DataFusionError::Execution(format!(
                    "RLS 安全校验失败: 表 '{}' 的行级过滤条件在查询优化过程中被移除，\
                     且未被 SecureTableProvider 兜底覆盖。查询被拒绝以防止数据泄露。",
                    rls_table
                )));
            }
        }
        Ok(())
    }

    /// Check if a Filter node exists directly above a TableScan for the given table
    fn table_has_filter_above(&self, plan: &LogicalPlan, table_name: &str) -> bool {
        let mut found = false;
        let _ = plan.apply(|node| {
            if let LogicalPlan::Filter(filter) = node {
                if let LogicalPlan::TableScan(scan) = filter.input.as_ref() {
                    if self.scan_matches(scan, table_name) {
                        found = true;
                        return Ok(TreeNodeRecursion::Stop);
                    }
                }
            }
            Ok(TreeNodeRecursion::Continue)
        });
        found
    }

    /// Check if any TableScan for the given table uses SecureTableProvider
    fn table_is_secure(&self, plan: &LogicalPlan, table_name: &str) -> bool {
        let mut found = false;
        let _ = plan.apply(|node| {
            if let LogicalPlan::TableScan(scan) = node {
                if self.scan_matches(scan, table_name) && Self::is_secure_table(scan) {
                    found = true;
                    return Ok(TreeNodeRecursion::Stop);
                }
            }
            Ok(TreeNodeRecursion::Continue)
        });
        found
    }

    /// TableScan 是否命中某个策略键（变体匹配：裸名/db.table/ds.db.table）
    fn scan_matches(&self, scan: &TableScan, rls_key: &str) -> bool {
        crate::security::ref_key_variants(&scan.table_name, crate::security::DEFAULT_CATALOG)
            .iter()
            .any(|key| key == rls_key)
    }

    /// TableScan 是否属于任一 RLS 保护表
    fn scan_matches_rls(&self, scan: &TableScan) -> bool {
        crate::security::ref_key_variants(&scan.table_name, crate::security::DEFAULT_CATALOG)
            .iter()
            .any(|key| self.rls_tables.contains(key))
    }
}

impl OptimizerRule for RLSVerifierRule {
    fn name(&self) -> &str {
        "RLSVerifierRule"
    }

    fn supports_rewrite(&self) -> bool {
        // 声明走 rewrite 路径: 与下方 rewrite() 实现一致。此前返回 false 与 rewrite()
        // 实现冲突, 优化器按旧 optimize 路径调用其默认实现, 恒抛
        // "Internal error: Should have called rewrite" 导致 DataEngine 查询全败。
        true
    }

    fn rewrite(
        &self,
        plan: LogicalPlan,
        _config: &dyn OptimizerConfig,
    ) -> Result<Transformed<LogicalPlan>, DataFusionError> {
        // Verify RLS enforcement — does not modify the plan
        self.verify_rls(&plan)?;
        Ok(Transformed::no(plan))
    }
}

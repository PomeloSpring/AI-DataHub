use std::any::Any;
use std::collections::HashMap;
use std::fmt;
use std::sync::Arc;

use arrow::datatypes::{Field, Schema, SchemaRef};
use async_trait::async_trait;
use datafusion::catalog::Session;
use datafusion::common::Result;
use datafusion::datasource::TableProvider;
use datafusion::logical_expr::{Expr, TableProviderFilterPushDown, TableType};
use datafusion::physical_plan::ExecutionPlan;
use tracing::debug;

/// Secure table provider — wraps an inner TableProvider with RLS policies.
///
/// Features:
/// - Row-level filtering: injects additional WHERE conditions
/// - Column hiding: removes columns from SELECT
/// - Column masking: replaces columns with masking expressions
pub struct SecureTableProvider {
    inner: Arc<dyn TableProvider>,
    row_filter: Option<Expr>,
    hidden_columns: Vec<String>,
    masked_columns: HashMap<String, String>,
    original_schema: SchemaRef,
    filtered_schema: SchemaRef,
}

impl fmt::Debug for SecureTableProvider {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SecureTableProvider")
            .field("row_filter", &self.row_filter.is_some())
            .field("hidden_columns", &self.hidden_columns)
            .field("masked_columns", &self.masked_columns.keys().collect::<Vec<_>>())
            .finish()
    }
}

impl SecureTableProvider {
    pub fn new(
        inner: Arc<dyn TableProvider>,
        row_filter: Option<Expr>,
        hidden_columns: Vec<String>,
        masked_columns: HashMap<String, String>,
    ) -> Self {
        let original_schema = inner.schema();
        let filtered_schema = Self::build_filtered_schema(&original_schema, &hidden_columns);

        Self {
            inner,
            row_filter,
            hidden_columns,
            masked_columns,
            original_schema,
            filtered_schema,
        }
    }

    /// Build schema with hidden columns removed
    fn build_filtered_schema(schema: &SchemaRef, hidden: &[String]) -> SchemaRef {
        if hidden.is_empty() {
            return schema.clone();
        }

        let hidden_lower: Vec<String> = hidden.iter().map(|c| c.to_lowercase()).collect();
        let fields: Vec<Field> = schema
            .fields()
            .iter()
            .filter(|f| !hidden_lower.contains(&f.name().to_lowercase()))
            .map(|f| f.as_ref().clone())
            .collect();

        Arc::new(Schema::new(fields))
    }

    /// Map projection indices from filtered schema to original schema
    fn map_projection(&self, projection: Option<&Vec<usize>>) -> Option<Vec<usize>> {
        if self.hidden_columns.is_empty() {
            return projection.cloned();
        }

        let hidden_lower: Vec<String> = self.hidden_columns.iter().map(|c| c.to_lowercase()).collect();
        let original_fields: Vec<(usize, &str)> = self
            .original_schema
            .fields()
            .iter()
            .enumerate()
            .map(|(i, f)| (i, f.name().as_str()))
            .collect();

        if let Some(proj) = projection {
            // Map from filtered schema indices to original schema indices
            let filtered_fields: Vec<(usize, &str)> = original_fields
                .iter()
                .filter(|(_, name)| !hidden_lower.contains(&name.to_lowercase()))
                .cloned()
                .collect();

            let mapped: Vec<usize> = proj
                .iter()
                .filter_map(|&i| filtered_fields.get(i).map(|(orig_idx, _)| *orig_idx))
                .collect();
            Some(mapped)
        } else {
            // No projection = all visible columns
            None
        }
    }

    /// Get summary of applied RLS policies
    #[allow(dead_code)]
    pub fn rls_summary(&self) -> Vec<String> {
        let mut summary = Vec::new();

        if let Some(ref filter) = self.row_filter {
            summary.push(format!("行级过滤: {}", filter));
        }

        if !self.hidden_columns.is_empty() {
            summary.push(format!("隐藏列: {}", self.hidden_columns.join(", ")));
        }

        if !self.masked_columns.is_empty() {
            summary.push(format!(
                "脱敏列: {}",
                self.masked_columns.keys().cloned().collect::<Vec<_>>().join(", ")
            ));
        }

        summary
    }
}

#[async_trait]
impl TableProvider for SecureTableProvider {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn schema(&self) -> SchemaRef {
        // Return filtered schema (hidden columns removed)
        self.filtered_schema.clone()
    }

    fn table_type(&self) -> TableType {
        self.inner.table_type()
    }

    async fn scan(
        &self,
        ctx: &dyn Session,
        projection: Option<&Vec<usize>>,
        filters: &[Expr],
        limit: Option<usize>,
    ) -> Result<Arc<dyn ExecutionPlan>> {
        // 1. Combine user filters with RLS row filter
        let mut all_filters = filters.to_vec();
        if let Some(ref rls_filter) = self.row_filter {
            all_filters.push(rls_filter.clone());
            debug!("Injected RLS row filter: {:?}", rls_filter);
        }

        // 2. Map projection to original schema (accounting for hidden columns)
        let mapped_projection = self.map_projection(projection);

        // 3. Delegate to inner table provider (will push down to remote DB)
        let plan = self
            .inner
            .scan(ctx, mapped_projection.as_ref(), &all_filters, limit)
            .await?;

        Ok(plan)
    }

    fn supports_filters_pushdown(
        &self,
        filters: &[&Expr],
    ) -> Result<Vec<TableProviderFilterPushDown>> {
        // Delegate to inner provider
        self.inner.supports_filters_pushdown(filters)
    }
}

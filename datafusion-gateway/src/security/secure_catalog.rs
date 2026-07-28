use std::any::Any;
use std::collections::HashMap;
use std::sync::Arc;

use async_trait::async_trait;
use datafusion::catalog::{CatalogProvider, SchemaProvider};
use datafusion::common::Result;
use datafusion::datasource::TableProvider;

/// Per-request secure catalog.
///
/// Wraps all tables with RLS policies. Each request gets its own catalog
/// instance with user-specific security context.
pub struct SecureCatalog {
    tables: HashMap<String, Arc<dyn TableProvider>>,
}

impl SecureCatalog {
    pub fn new(tables: HashMap<String, Arc<dyn TableProvider>>) -> Self {
        Self { tables }
    }
}

impl CatalogProvider for SecureCatalog {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn schema_names(&self) -> Vec<String> {
        vec!["public".to_string()]
    }

    fn schema(&self, _name: &str) -> Option<Arc<dyn SchemaProvider>> {
        Some(Arc::new(SecureSchema {
            tables: self.tables.clone(),
        }))
    }
}

struct SecureSchema {
    tables: HashMap<String, Arc<dyn TableProvider>>,
}

#[async_trait]
impl SchemaProvider for SecureSchema {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn table_names(&self) -> Vec<String> {
        self.tables.keys().cloned().collect()
    }

    async fn table(&self, name: &str) -> Result<Option<Arc<dyn TableProvider>>> {
        Ok(self.tables.get(name).cloned())
    }

    fn table_exist(&self, name: &str) -> bool {
        self.tables.contains_key(name)
    }
}

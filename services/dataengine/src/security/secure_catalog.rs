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
///
/// Schema 布局（跨源联邦）：
/// - "public"：该源全部表（供裸表名 `t` 解析，DEFAULT_CATALOG 默认 schema）
/// - 每个 qualifier（数据库/namespace）一个 schema：供双段限定名 `db.t` 解析
/// - 联邦源注册为独立 catalog（catalog 名 = 数据源名），三段式 `ds.db.t` 解析
pub struct SecureCatalog {
    /// schema 名 -> (表名 -> provider)；"public" 为全量视图
    schemas: HashMap<String, HashMap<String, Arc<dyn TableProvider>>>,
}

impl SecureCatalog {
    /// 兼容旧签名：单 schema（全部表放 "public"）。
    #[allow(dead_code)]
    pub fn new(tables: HashMap<String, Arc<dyn TableProvider>>) -> Self {
        let mut schemas = HashMap::new();
        schemas.insert("public".to_string(), tables);
        Self { schemas }
    }

    /// 多 schema 构造：groups 的 key 为 qualifier（数据库/namespace 名）。
    /// 自动补 "public" 全量视图供裸表名解析。
    /// 注意：同源跨库同名表在 "public" 视图会冲突（后写覆盖）——裸名引用
    /// 同名表本身即歧义，应使用限定名，此处不做猜测消歧。
    pub fn from_groups(
        groups: HashMap<String, HashMap<String, Arc<dyn TableProvider>>>,
    ) -> Self {
        let mut all: HashMap<String, Arc<dyn TableProvider>> = HashMap::new();
        for tables in groups.values() {
            for (name, provider) in tables {
                all.insert(name.clone(), provider.clone());
            }
        }
        let mut schemas = groups;
        schemas.insert("public".to_string(), all);
        Self { schemas }
    }

    /// schema 名大小写不敏感查找（SQL 标识符解析后多为小写）。
    fn find_schema(&self, name: &str) -> Option<&HashMap<String, Arc<dyn TableProvider>>> {
        self.schemas
            .get(name)
            .or_else(|| self.schemas.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v))
    }
}

impl CatalogProvider for SecureCatalog {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn schema_names(&self) -> Vec<String> {
        self.schemas.keys().cloned().collect()
    }

    fn schema(&self, name: &str) -> Option<Arc<dyn SchemaProvider>> {
        self.find_schema(name)
            .map(|tables| Arc::new(SecureSchema { tables: tables.clone() }) as Arc<dyn SchemaProvider>)
    }
}

struct SecureSchema {
    tables: HashMap<String, Arc<dyn TableProvider>>,
}

impl SecureSchema {
    fn find_table(&self, name: &str) -> Option<Arc<dyn TableProvider>> {
        self.tables
            .get(name)
            .cloned()
            .or_else(|| {
                self.tables
                    .iter()
                    .find(|(k, _)| k.eq_ignore_ascii_case(name))
                    .map(|(_, v)| v.clone())
            })
    }
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
        Ok(self.find_table(name))
    }

    fn table_exist(&self, name: &str) -> bool {
        self.find_table(name).is_some()
    }
}

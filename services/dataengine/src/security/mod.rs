pub mod secure_table;
pub mod secure_catalog;
pub mod rules;

pub use secure_table::SecureTableProvider;
pub use secure_catalog::SecureCatalog;
pub use rules::{RLSAnalyzerRule, RLSVerifierRule};

/// 默认 catalog 名（= DataFusion DEFAULT_CATALOG，主源注册于此，裸表名解析）
pub const DEFAULT_CATALOG: &str = "datafusion";

/// 限定表引用的策略匹配键变体（护栏 §10：匹配键按解析后的 (源, 表) 归一）。
///
/// Python 侧 RLS 策略的 `tables` 键与 SQL 中的写法一致（裸名 / db.table /
/// ds.db.table），而计划里的 TableReference 是解析后的三元组 —— 这里生成
/// 该表所有可能的策略键（小写），逐个查找，跨库同名表不串味：
/// - 主源表（catalog 缺省或 = datafusion）：`t`、`db.t`
/// - 联邦源表（catalog = 数据源名）：`ds.db.t`、`ds.t`（后者不产出，SQL
///   两段式解析为 schema.table 而非 catalog.table，与 enforcer 语义一致）
pub fn ref_key_variants(
    tref: &datafusion::sql::TableReference,
    default_catalog: &str,
) -> Vec<String> {
    let name = tref.table().to_lowercase();
    let mut keys = Vec::with_capacity(2);
    match (tref.catalog(), tref.schema()) {
        (Some(catalog), Some(db)) if catalog.to_lowercase() != default_catalog => {
            keys.push(format!("{}.{}.{}", catalog.to_lowercase(), db.to_lowercase(), name));
        }
        (_, Some(db)) => {
            keys.push(format!("{}.{}", db.to_lowercase(), name));
            keys.push(name);
        }
        (_, None) => {
            keys.push(name);
        }
    }
    keys
}

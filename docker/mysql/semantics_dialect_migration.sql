-- ============================================================================
-- Semantic Layer · 多方言编译 + 数据集绑定 (迁移)
--
-- 配合"语义层按数据源 db_type 方言编译(mysql/doris→MySQL 族, postgres/sls→PG 族)"
-- 与"本体对象绑定查询数据集(bind_kind='dataset')"落地:
--   1. adh_metrics.formula_dialects     多方言函数模板指标(高级分析函数载体)
--      键为引擎名('doris'/'sls')或编译方言族('mysql'(含 doris 语义族) / 'postgres'(含 sls));
--      取用优先级 引擎 → 方言族 → 通用; 键存在值为 null=显式不支持。
--   2. adh_ontology_bindings.bind_kind ENUM 扩 'dataset'（预制 SQL 走数据集，
--      dataset_ref 列见 dataset_sql_migration.sql / semantic_layer_migration.sql）
--   3. adh_datasources.ssl_mode         PG/SLS 数据源 TLS 模式(disabled|prefer|require|...)
--
-- 历史注：早前版本的 2b/3 段（adh_ontology_bindings.template_ref + adh_sql_templates.dialect）
-- 与第 5 段（Doris window_funnel 演示指标）已随预制 SQL 收敛到数据集而退役，
-- 连同 adh_sql_templates 表一起见 dataset_sql_migration.sql。
--
-- 约定: 与 semantic_layer_migration.sql 一致, 全部 DDL 幂等(MySQL 无
--   ADD COLUMN IF NOT EXISTS, 用 information_schema 守卫)。
-- Run: mysql -h <mysql_host> -P <port> -u <user> -p <METADATA_DB_DATABASE> \
--        < semantics_dialect_migration.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. adh_metrics: 多方言函数模板（键: 引擎名或方言族名）
-- ----------------------------------------------------------------------------
SET @has_fd := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_metrics'
      AND COLUMN_NAME = 'formula_dialects'
);
SET @ddl := IF(@has_fd = 0,
    'ALTER TABLE adh_metrics ADD COLUMN formula_dialects JSON NULL COMMENT ''按方言注册的可下推表达式 {"mysql":...,"postgres":null}; 键存在值为 null=显式不支持该方言'' AFTER formula_dsl',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 2. adh_ontology_bindings: bind_kind ENUM 扩 'dataset' (MODIFY 幂等, 值集仅追加)
-- ----------------------------------------------------------------------------
SET @has_dataset := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_ontology_bindings'
      AND COLUMN_NAME = 'bind_kind'
      AND COLUMN_TYPE LIKE '%dataset%'
);
SET @ddl := IF(@has_dataset = 0,
    'ALTER TABLE adh_ontology_bindings MODIFY COLUMN bind_kind ENUM(''primary'',''lateral'',''bridge'',''metric_source'',''dataset'') NOT NULL DEFAULT ''primary''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 3. adh_datasources: ssl_mode (PG 语义 disabled/prefer/require; 兼容 rustls 取值)
-- ----------------------------------------------------------------------------
SET @has_sslmode := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_datasources'
      AND COLUMN_NAME = 'ssl_mode'
);
SET @ddl := IF(@has_sslmode = 0,
    'ALTER TABLE adh_datasources ADD COLUMN ssl_mode VARCHAR(16) NOT NULL DEFAULT ''disabled'' COMMENT ''TLS 模式: disabled|prefer|require|disable(PG语义)'' AFTER `ssl`',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

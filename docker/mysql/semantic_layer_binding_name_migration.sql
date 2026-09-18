-- ============================================================================
-- Semantic Layer · 数据源按 name 全局唯一绑定 (修正)
--
-- 背景:本体对象与物理表的执行绑定(binding)此前只用 `datasource_id` 关联。
--   数据源删除后重新新增会拿到新的自增 id, 旧 binding 便"孤儿化"再也关联不上。
-- 修正:设定 `adh_datasources.name` 为全局唯一标识, binding 侧同时持久化
--   `datasource_name`; 解析时以 name 映射到"当前有效"的 datasource id,
--   从而删除重建(同名)后仍可自动重新关联。
--
-- 约定:与 semantic_layer_migration.sql 一致, 全部 DDL 幂等(MySQL 无
--   ADD COLUMN/INDEX IF NOT EXISTS, 用 information_schema 守卫)。
-- Run: mysql -h <mysql_host> -P <port> -u <user> -p <METADATA_DB_DATABASE> \
--        < semantic_layer_binding_name_migration.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. adh_ontology_bindings: 增加 datasource_name (可空, 供按名重关联)
-- ----------------------------------------------------------------------------
SET @has_bname := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_ontology_bindings'
      AND COLUMN_NAME = 'datasource_name'
);
SET @ddl := IF(@has_bname = 0,
    'ALTER TABLE adh_ontology_bindings ADD COLUMN datasource_name VARCHAR(128) NULL COMMENT ''数据源名(全局唯一标识, 供删除重建后按名重关联)'' AFTER datasource_id',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 索引: 按 (datasource_name, status) 命中 binding
SET @has_bname_idx := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_ontology_bindings'
      AND INDEX_NAME = 'idx_bind_dsname'
);
SET @ddl := IF(@has_bname_idx = 0,
    'ALTER TABLE adh_ontology_bindings ADD INDEX idx_bind_dsname (datasource_name, status)',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 回填: 用现存 datasource_id 反查 name
UPDATE adh_ontology_bindings b
    JOIN adh_datasources d ON d.id = b.datasource_id
    SET b.datasource_name = d.name
WHERE (b.datasource_name IS NULL OR b.datasource_name = '');

-- ----------------------------------------------------------------------------
-- 2. adh_datasources.name: 全局唯一标识
--    仅在"无重名"时添加 UNIQUE(避免迁移因历史脏数据失败); 有重名则跳过并提示,
--    由运维先人工消歧。当前库经核对无重名。
-- ----------------------------------------------------------------------------
SET @has_ds_uniq := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_datasources'
      AND INDEX_NAME = 'uk_datasource_name'
);
SET @dup_cnt := (
    SELECT COUNT(*) FROM (
        SELECT name FROM adh_datasources GROUP BY name HAVING COUNT(*) > 1
    ) t
);
SET @ddl := IF(@has_ds_uniq = 0 AND @dup_cnt = 0,
    'ALTER TABLE adh_datasources ADD UNIQUE KEY uk_datasource_name (name)',
    'SELECT CONCAT(''skip uk_datasource_name (exists or dup names: '', @dup_cnt, '')'') AS msg');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

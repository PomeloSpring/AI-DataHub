-- ============================================================================
-- Semantic Layer · 多方言编译 + SQL 模板绑定 (迁移)
--
-- 配合"语义层按数据源 db_type 方言编译(mysql/doris→MySQL 族, postgres/sls→PG 族)"
-- 与"本体对象绑定 SQL 模板(bind_kind='sql_template')"落地:
--   1. adh_metrics.formula_dialects     多方言函数模板指标(漏斗/留存等高级函数载体)
--      键为编译方言族: 'mysql'(含 doris 语义族) / 'postgres'(含 sls); 值 null=显式不支持
--   2. adh_ontology_bindings.template_ref + bind_kind ENUM 扩 'sql_template'
--   3. adh_sql_templates.dialect        模板所属方言(mysql|postgres|generic)
--   4. adh_datasources.ssl_mode         PG/SLS 数据源 TLS 模式(disabled|prefer|require|...)
--
-- 约定: 与 semantic_layer_migration.sql 一致, 全部 DDL 幂等(MySQL 无
--   ADD COLUMN IF NOT EXISTS, 用 information_schema 守卫)。
--   planner/binding_resolver 对缺列均有运行时回落, 本迁移可先后可行。
-- Run: mysql -h <mysql_host> -P <port> -u <user> -p <METADATA_DB_DATABASE> \
--        < semantics_dialect_migration.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. adh_metrics: 多方言函数模板 (如 Doris window_funnel 漏斗)
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
-- 2a. adh_ontology_bindings: bind_kind ENUM 扩 'sql_template' (MODIFY 幂等, 值集不变仅追加)
-- ----------------------------------------------------------------------------
SET @has_sqltpl := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_ontology_bindings'
      AND COLUMN_NAME = 'bind_kind'
      AND COLUMN_TYPE LIKE '%sql_template%'
);
SET @ddl := IF(@has_sqltpl = 0,
    'ALTER TABLE adh_ontology_bindings MODIFY COLUMN bind_kind ENUM(''primary'',''lateral'',''bridge'',''metric_source'',''sql_template'') NOT NULL DEFAULT ''primary''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2b. template_ref: 指向 adh_sql_templates.template_id
SET @has_tref := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_ontology_bindings'
      AND COLUMN_NAME = 'template_ref'
);
SET @ddl := IF(@has_tref = 0,
    'ALTER TABLE adh_ontology_bindings ADD COLUMN template_ref VARCHAR(64) NULL COMMENT ''bind_kind=sql_template 时指向 adh_sql_templates.template_id'' AFTER bind_kind',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 3. adh_sql_templates: 方言标注 (generic=各方言均可用, 由建模人员保证)
--    存量模板均为 MySQL 方言演示语料, 默认 '' -> 装载侧视同 mysql。
-- ----------------------------------------------------------------------------
SET @has_tdialect := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_sql_templates'
      AND COLUMN_NAME = 'dialect'
);
SET @ddl := IF(@has_tdialect = 0,
    'ALTER TABLE adh_sql_templates ADD COLUMN dialect VARCHAR(16) NOT NULL DEFAULT '''' COMMENT ''mysql|postgres|generic; 空视同 mysql'' AFTER sql_template',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 4. adh_datasources: ssl_mode (PG 语义 disabled/prefer/require; 兼容 rustls 取值)
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

-- ----------------------------------------------------------------------------
-- 5. 种子: 演示事件表(t_case_records)的 Doris window_funnel 漏斗指标
--    formula_dialects 显式声明 mysql=null / postgres=null: 无 Doris 演示数据源时,
--    普通 MySQL/PG 数据源上编译出明确"不支持"warning, 而非下发非法函数。
--    幂等: 按 (target_table, name_en) 判重, 可重复执行。
-- ----------------------------------------------------------------------------
SET @has_funnel := (
    SELECT COUNT(*) FROM adh_metrics
    WHERE target_table = 't_case_records' AND name_en = 'case_funnel_complete'
);
SET @ddl := IF(@has_funnel = 0,
    'INSERT INTO adh_metrics (workspace_id, name, name_en, formula, formula_dialects, unit, agg_type, target_table, target_column, description, category, datasource_id, is_active, default_agg, bound_object_key) VALUES (1, ''案例转化漏斗(完成数)'', ''case_funnel_complete'', NULL, ''{""mysql"": null, ""postgres"": null, ""doris"": ""window_funnel(30, ''''strict'''')(create_time, CASE WHEN del_flag = 0 THEN 1 ELSE 2 END) = 2""}''', ''人'', ''CUSTOM'', ''t_case_records'', ''id'', ''30 天严格两步漏斗: 案例事件(del_flag=0)按 create_time 排序判定, 仅 Doris 数据源可执行'', ''分析'', 0, 1, NULL, ''case'')',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

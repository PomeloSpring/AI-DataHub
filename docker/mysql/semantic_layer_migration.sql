-- ============================================================================
-- Semantic Layer Migration
-- BI/ChatBI -> 语义层 -> 执行层 三层架构的语义层数据基础 (Phase 0)
--
-- 目标:
--   * 建立 catalog 层级(承载 Doris 三段式联邦引用,根治 manifest catalog 硬编码)
--   * 本体对象 <-> 物理表 执行绑定 (adh_ontology_bindings)
--   * 表/列成本分级 + 查询模式 + 护栏 (size_class / query_mode / allow_full_scan)
--   * 口径 SSoT (adh_metrics/dimensions 扩列)
--   * 元数据双向同步对账 (adh_meta_sync_state)
--   * 大屏语义化 (adh_charts.semantic_query)
--
-- 约定:所有新增列均给"保持现有行为"的默认值,不破坏既有读写路径。
-- Run: mysql -h <mysql_host> -P <port> -u <user> -p <METADATA_DB_DATABASE> \
--        < semantic_layer_migration.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. catalog 层级 (Doris internal / jdbc / es / hive / iceberg)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_catalogs (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    datasource_id       BIGINT NOT NULL DEFAULT 0,
    catalog_name        VARCHAR(128) NOT NULL,
    catalog_type        ENUM('internal','jdbc','es','hive','iceberg') NOT NULL DEFAULT 'internal',
    remote_props        JSON COMMENT 'Doris CREATE CATALOG 的 jdbc/es 连接参数',
    federation_enabled  TINYINT DEFAULT 0,
    is_active           TINYINT DEFAULT 1,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_catalog (datasource_id, catalog_name),
    INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据源 catalog 层级(联邦引用)';

ALTER TABLE adh_datasources
    ADD COLUMN default_catalog_id BIGINT NULL COMMENT '该数据源默认 catalog';

-- ----------------------------------------------------------------------------
-- 2. 表级:成本分级 + 查询模式 + 联邦定位 + NoETL 明细层标记
-- ----------------------------------------------------------------------------
ALTER TABLE adh_table_info
    ADD COLUMN catalog_id          BIGINT NULL COMMENT '所属 catalog (adh_catalogs.id)',
    ADD COLUMN size_class          ENUM('small','large','huge') NULL COMMENT '成本分级',
    ADD COLUMN est_rows            BIGINT NULL COMMENT '估算行数',
    ADD COLUMN query_mode          ENUM('materialized','federated','raw_source') NOT NULL DEFAULT 'materialized',
    ADD COLUMN allow_full_scan     TINYINT NOT NULL DEFAULT 1 COMMENT '是否允许全表扫描',
    ADD COLUMN source_datasource_id BIGINT NULL COMMENT '物理来源数据源(元数据在此、数据在别处)',
    ADD COLUMN grain               ENUM('detail','sum') NOT NULL DEFAULT 'detail' COMMENT '明细/汇总',
    ADD COLUMN dw_layer            ENUM('ods','dwd','dws','ads') NULL COMMENT '数仓分层(NoETL 明细层)';

-- ----------------------------------------------------------------------------
-- 3. 列级:语义角色 + 脱敏策略
-- ----------------------------------------------------------------------------
ALTER TABLE adh_column_metadata
    ADD COLUMN semantic_role  ENUM('key','fk','measure','dimension','time') NULL,
    ADD COLUMN masking_policy VARCHAR(64) NULL COMMENT '列脱敏策略',
    ADD COLUMN unit           VARCHAR(16) NULL,
    ADD COLUMN enum_values    JSON NULL;

-- ----------------------------------------------------------------------------
-- 4. 本体对象 <-> 物理表 执行绑定 (execution_binding 的结构化落地面)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_ontology_bindings (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    model_id            BIGINT NOT NULL,
    object_key          VARCHAR(128) NOT NULL,
    datasource_id       BIGINT NOT NULL DEFAULT 0,
    catalog_ref         VARCHAR(256) COMMENT 'catalog.db.table 三段式引用',
    bind_kind           ENUM('primary','lateral','bridge','metric_source') NOT NULL DEFAULT 'primary',
    physical_table      VARCHAR(128),
    join_expr           TEXT,
    column_map          JSON COMMENT '{对象属性: 物理列}',
    query_mode          ENUM('materialized','federated','raw_source') NOT NULL DEFAULT 'materialized',
    size_class          ENUM('small','large','huge') NULL,
    allow_full_scan     TINYINT NOT NULL DEFAULT 1,
    max_rows            INT NULL,
    timeout_sec         INT NULL,
    permission_tokens   JSON,
    rls_policy_ref      VARCHAR(64) NULL,
    masked_columns      JSON,
    sync_state          ENUM('bound','drifted','orphaned','unbound') NOT NULL DEFAULT 'bound',
    last_verified_at    DATETIME NULL,
    status              ENUM('active','deprecated') NOT NULL DEFAULT 'active',
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_bind_object (model_id, object_key),
    INDEX idx_bind_ds (datasource_id, status),
    INDEX idx_bind_sync (sync_state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体对象执行绑定(binding)';

-- 本体对象表补一个内联 binding JSON 段(与规范化表互为便利视图,导入器写此处)
ALTER TABLE adh_ontology_objects
    ADD COLUMN execution_binding JSON NULL COMMENT '对象执行绑定内联快照';

-- ----------------------------------------------------------------------------
-- 5. 口径 SSoT (指标/维度)
-- ----------------------------------------------------------------------------
ALTER TABLE adh_metrics
    ADD COLUMN certified        TINYINT NOT NULL DEFAULT 0 COMMENT '是否认证口径(单一事实源)',
    ADD COLUMN owner_role       VARCHAR(64) NULL,
    ADD COLUMN default_agg      VARCHAR(32) NULL,
    ADD COLUMN bound_object_key VARCHAR(128) NULL COMMENT '归属本体对象',
    ADD COLUMN formula_dsl      TEXT NULL COMMENT '可下推的聚合表达式';

ALTER TABLE adh_dimensions
    ADD COLUMN certified        TINYINT NOT NULL DEFAULT 0,
    ADD COLUMN owner_role       VARCHAR(64) NULL,
    ADD COLUMN bound_object_key VARCHAR(128) NULL;

-- ----------------------------------------------------------------------------
-- 6. 元数据双向同步对账状态 (反向:information_schema 扫描 -> 漂移检测)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_meta_sync_state (
    datasource_id     BIGINT NOT NULL,
    catalog_id        BIGINT NOT NULL DEFAULT 0,
    table_name        VARCHAR(128) NOT NULL,
    source_checksum   VARCHAR(64) NULL COMMENT '物理 schema 指纹',
    source_updated_at DATETIME NULL,
    synced_at         DATETIME NULL,
    drift             ENUM('none','schema_changed','dropped','new') NOT NULL DEFAULT 'none',
    action_taken      VARCHAR(64) NULL,
    PRIMARY KEY (datasource_id, catalog_id, table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='元数据同步漂移对账';

-- ----------------------------------------------------------------------------
-- 7. 大屏语义化 (chart 存 SemanticQuery 而非裸 SQL)
-- ----------------------------------------------------------------------------
ALTER TABLE adh_charts
    ADD COLUMN semantic_query JSON NULL COMMENT '声明式 SemanticQuery',
    ADD COLUMN query_source   ENUM('raw_sql','semantic') NOT NULL DEFAULT 'raw_sql';

-- ----------------------------------------------------------------------------
-- 8. Phase 4 · 语义查询审计闸门 (gate 7)
--    早期部署的 adh_query_audit 缺 query_type 列, 导致 log_audit 一直静默失败;
--    补列让 semantic / sql / rest / dsl 各类查询都能留痕。MySQL 无 ADD COLUMN
--    IF NOT EXISTS, 用 information_schema 守卫保证幂等。
-- ----------------------------------------------------------------------------
SET @has_qt := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_query_audit'
      AND COLUMN_NAME = 'query_type'
);
SET @ddl := IF(@has_qt = 0,
    'ALTER TABLE adh_query_audit ADD COLUMN query_type VARCHAR(16) DEFAULT ''sql'' AFTER generated_sql',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ═══════════════════════════════════════════════════════════════
-- 数据建模可视化模块 — 建表 SQL
-- 执行: mysql -u root -p < modeling_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh;

-- 数据模型表
CREATE TABLE IF NOT EXISTS adh_data_models (
    id              BIGINT PRIMARY KEY,
    workspace_id    BIGINT NOT NULL DEFAULT 0 COMMENT '所属工作空间',
    datasource_id   BIGINT NOT NULL COMMENT '目标数据源',
    model_name      VARCHAR(128) NOT NULL COMMENT '模型名称',
    description     TEXT COMMENT '模型描述',
    is_active       TINYINT DEFAULT 1 COMMENT '是否启用',
    created_by      BIGINT COMMENT '创建人',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace_ds (workspace_id, datasource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据模型';

-- 模型表配置
CREATE TABLE IF NOT EXISTS adh_model_tables (
    id              BIGINT PRIMARY KEY,
    model_id        BIGINT NOT NULL COMMENT '关联模型 ID',
    table_name      VARCHAR(128) NOT NULL COMMENT '表名',
    display_name    VARCHAR(128) COMMENT '显示名称',
    business_desc   TEXT COMMENT '业务描述',
    is_visible      TINYINT DEFAULT 1 COMMENT '是否在画布中显示',
    position_x      FLOAT DEFAULT 0 COMMENT '画布 X 坐标',
    position_y      FLOAT DEFAULT 0 COMMENT '画布 Y 坐标',
    INDEX idx_model (model_id),
    UNIQUE KEY uk_model_table (model_id, table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型表配置';

-- 模型关系
CREATE TABLE IF NOT EXISTS adh_model_relations (
    id              BIGINT PRIMARY KEY,
    model_id        BIGINT NOT NULL COMMENT '关联模型 ID',
    source_table    VARCHAR(128) NOT NULL COMMENT '源表',
    source_column   VARCHAR(128) NOT NULL COMMENT '源列',
    target_table    VARCHAR(128) NOT NULL COMMENT '目标表',
    target_column   VARCHAR(128) NOT NULL COMMENT '目标列',
    join_type       VARCHAR(32) DEFAULT 'INNER' COMMENT 'JOIN 类型: INNER / LEFT / RIGHT / FULL',
    relation_type   VARCHAR(32) DEFAULT '1:N' COMMENT '关系类型: 1:1 / 1:N / N:N',
    description     VARCHAR(256) COMMENT '关系描述',
    is_active       TINYINT DEFAULT 1 COMMENT '是否启用',
    INDEX idx_model (model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模型关系';

-- 计算字段
CREATE TABLE IF NOT EXISTS adh_calculated_fields (
    id              BIGINT PRIMARY KEY,
    model_id        BIGINT NOT NULL COMMENT '关联模型 ID',
    table_name      VARCHAR(128) NOT NULL COMMENT '所属表',
    field_name      VARCHAR(128) NOT NULL COMMENT '字段名',
    display_name    VARCHAR(128) COMMENT '显示名称',
    expression      TEXT NOT NULL COMMENT '计算表达式',
    data_type       VARCHAR(32) COMMENT '数据类型: number / string / date',
    description     TEXT COMMENT '描述',
    is_active       TINYINT DEFAULT 1 COMMENT '是否启用',
    INDEX idx_model_table (model_id, table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='计算字段';

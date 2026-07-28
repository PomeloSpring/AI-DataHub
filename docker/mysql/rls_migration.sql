-- ═══════════════════════════════════════════════════════════════
-- RLS (行级安全) 模块 — 建表 SQL
-- 执行: mysql -u root -p < rls_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh;

-- RLS 策略表
CREATE TABLE IF NOT EXISTS adh_rls_policies (
    id              BIGINT PRIMARY KEY,
    name            VARCHAR(128) NOT NULL COMMENT '策略名称',
    description     TEXT COMMENT '策略描述',
    workspace_id    BIGINT NOT NULL COMMENT '所属工作空间',
    datasource_id   BIGINT NOT NULL COMMENT '目标数据源',
    table_name      VARCHAR(128) NOT NULL COMMENT '目标表名',
    policy_type     VARCHAR(32) NOT NULL DEFAULT 'both' COMMENT '策略类型: row / column / both',
    filter_type     VARCHAR(32) NOT NULL DEFAULT 'condition' COMMENT '过滤类型: condition / user_attribute',
    filter_expr     TEXT COMMENT '行过滤表达式（SQL WHERE 片段），如 "region = :user_region"',
    user_attribute  VARCHAR(64) COMMENT '关联的用户属性名，如 region',
    is_active       TINYINT DEFAULT 1 COMMENT '是否启用',
    created_by      BIGINT COMMENT '创建人',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace_table (workspace_id, table_name),
    INDEX idx_datasource (datasource_id),
    INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='RLS 行级安全策略';

-- RLS 列权限表
CREATE TABLE IF NOT EXISTS adh_rls_column_policies (
    id              BIGINT PRIMARY KEY,
    policy_id       BIGINT NOT NULL COMMENT '关联 adh_rls_policies.id',
    column_name     VARCHAR(128) NOT NULL COMMENT '列名',
    access_type     VARCHAR(32) NOT NULL DEFAULT 'visible' COMMENT '访问类型: visible / hidden / masked',
    mask_pattern    VARCHAR(64) COMMENT '脱敏模式，如 phone -> partial',
    description     VARCHAR(256) COMMENT '说明',
    INDEX idx_policy (policy_id),
    UNIQUE KEY uk_policy_column (policy_id, column_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='RLS 列级权限';

-- RLS 用户属性表（用于动态行过滤）
CREATE TABLE IF NOT EXISTS adh_rls_user_attributes (
    id              BIGINT PRIMARY KEY,
    user_id         BIGINT NOT NULL COMMENT '用户 ID',
    workspace_id    BIGINT NOT NULL COMMENT '工作空间 ID',
    attr_key        VARCHAR(64) NOT NULL COMMENT '属性名，如 region / department',
    attr_value      VARCHAR(256) NOT NULL COMMENT '属性值',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_attr (user_id, workspace_id, attr_key),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='RLS 用户属性';

-- RLS 审计日志
CREATE TABLE IF NOT EXISTS adh_rls_audit_logs (
    id              BIGINT PRIMARY KEY,
    user_id         BIGINT NOT NULL COMMENT '用户 ID',
    workspace_id    BIGINT NOT NULL COMMENT '工作空间 ID',
    policy_id       BIGINT COMMENT '触发的策略 ID',
    policy_name     VARCHAR(128) COMMENT '策略名称（冗余）',
    table_name      VARCHAR(128) COMMENT '涉及的表名',
    action          VARCHAR(32) NOT NULL COMMENT '动作: row_filter / column_hide / column_mask',
    original_sql    TEXT COMMENT '原始 SQL',
    filtered_sql    TEXT COMMENT '过滤后的 SQL',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user_time (user_id, created_at),
    INDEX idx_workspace (workspace_id),
    INDEX idx_policy (policy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='RLS 审计日志';

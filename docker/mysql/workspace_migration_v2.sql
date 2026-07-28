-- Workspace Migration Script v2
-- Creates workspace tables and adds workspace_id to all existing tables

USE adh;

-- ============================================================================
-- 1. 创建工作空间表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workspaces (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    name            VARCHAR(100) NOT NULL,
    description     VARCHAR(500) DEFAULT '',
    icon            VARCHAR(50)  DEFAULT '📊',
    color           VARCHAR(20)  DEFAULT '#1890ff',
    owner_id        BIGINT       NOT NULL,
    is_default      TINYINT      DEFAULT 0,
    config          JSON,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_owner (owner_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 2. 创建工作空间-用户关联表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workspace_users (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    workspace_id    BIGINT NOT NULL,
    user_id         BIGINT NOT NULL,
    role            VARCHAR(32)  DEFAULT 'member',
    is_default      TINYINT      DEFAULT 0,
    joined_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ws_user (workspace_id, user_id),
    INDEX idx_workspace (workspace_id),
    INDEX idx_user (user_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 3. 创建工作空间-数据源关联表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workspace_datasources (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    workspace_id    BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL,
    is_primary      TINYINT DEFAULT 0,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ws_ds (workspace_id, datasource_id),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 4. 修改现有表添加 workspace_id
-- ============================================================================

-- 元数据表
ALTER TABLE adh_table_info ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER datasource_id;
ALTER TABLE adh_column_metadata ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER datasource_id;
ALTER TABLE adh_sql_templates ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER datasource_id;
ALTER TABLE adh_business_terms ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER datasource_id;
ALTER TABLE adh_table_relations ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER datasource_id;

-- 仪表盘和图表
ALTER TABLE adh_dashboards ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER owner_id;
ALTER TABLE adh_charts ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER dashboard_id;
ALTER TABLE adh_chart_snapshots ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER user_id;

-- 对话和查询
ALTER TABLE adh_conversations ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER user_id;
ALTER TABLE adh_saved_queries ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER owner_id;

-- MCP 和 Agent
ALTER TABLE adh_mcp_servers ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER id;
ALTER TABLE adh_agents ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER id;

-- Prompt
ALTER TABLE adh_prompts ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER id;
ALTER TABLE adh_prompt_versions ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER prompt_id;

-- 工作流
ALTER TABLE adh_workflow_configs ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER id;
ALTER TABLE adh_workflow_steps ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER workflow_id;
ALTER TABLE adh_workflow_logs ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER workflow_id;

-- 审计和指标
ALTER TABLE adh_query_audit ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER datasource_id;
ALTER TABLE adh_audit_logs ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER user_id;
ALTER TABLE adh_pipeline_metrics ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER user_id;
ALTER TABLE adh_sql_corrections ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER datasource_id;

-- 菜单
ALTER TABLE adh_menu_items ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER id;

-- ============================================================================
-- 5. 添加索引
-- ============================================================================
ALTER TABLE adh_table_info ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_column_metadata ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_sql_templates ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_business_terms ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_table_relations ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_dashboards ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_charts ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_chart_snapshots ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_conversations ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_saved_queries ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_mcp_servers ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_agents ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_prompts ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_prompt_versions ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_workflow_configs ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_workflow_steps ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_workflow_logs ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_query_audit ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_audit_logs ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_pipeline_metrics ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_sql_corrections ADD INDEX idx_workspace (workspace_id);
ALTER TABLE adh_menu_items ADD INDEX idx_workspace (workspace_id);

-- ============================================================================
-- 6. 数据迁移
-- ============================================================================

-- 创建默认工作空间
INSERT INTO adh_workspaces (id, name, description, icon, owner_id, is_default)
VALUES (1, '默认工作空间', '系统自动创建的默认工作空间', '🏠', 1, 1);

-- 将所有用户关联到默认工作空间
INSERT INTO adh_workspace_users (workspace_id, user_id, role, is_default)
SELECT 1, id, CASE WHEN id = 1 THEN 'owner' ELSE 'member' END, 1
FROM adh_users;

-- 将所有数据源关联到默认工作空间
INSERT INTO adh_workspace_datasources (workspace_id, datasource_id, is_primary)
SELECT 1, id, is_default FROM adh_datasources;

-- 更新所有表的 workspace_id 为 1（默认工作空间）
UPDATE adh_table_info SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_column_metadata SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_sql_templates SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_business_terms SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_table_relations SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_dashboards SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_charts SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_chart_snapshots SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_conversations SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_saved_queries SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_mcp_servers SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_agents SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_prompts SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_workflow_configs SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_menu_items SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_query_audit SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_audit_logs SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_pipeline_metrics SET workspace_id = 1 WHERE workspace_id = 0;
UPDATE adh_sql_corrections SET workspace_id = 1 WHERE workspace_id = 0;

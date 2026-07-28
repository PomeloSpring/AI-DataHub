-- Workspace Migration Script
-- Creates workspace-related tables and migrates existing data

USE adh;

-- ============================================================================
-- 工作空间表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workspaces (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    name            VARCHAR(100) NOT NULL,
    description     VARCHAR(500) DEFAULT '',
    workspace_type  VARCHAR(32)  DEFAULT 'custom',  -- data_analysis, log_analysis, ops, custom
    user_id         BIGINT       NOT NULL,           -- 创建者
    is_default      TINYINT      DEFAULT 0,          -- 用户默认工作空间
    is_public       TINYINT      DEFAULT 0,          -- 是否公开（所有用户可见）
    config          JSON,                             -- 额外配置（LLM模型、检索策略等）
    icon            VARCHAR(50)  DEFAULT '📊',       -- 工作空间图标
    color           VARCHAR(20)  DEFAULT '#1890ff',  -- 主题色
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_user (user_id),
    INDEX idx_type (workspace_type)
) ENGINE=InnoDB;

-- ============================================================================
-- 工作空间-数据源关联表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workspace_datasources (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    workspace_id    BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL,
    is_primary      TINYINT DEFAULT 0,    -- 主数据源（元数据检索优先）
    alias           VARCHAR(100) DEFAULT '', -- 在此工作空间内的别名
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ws_ds (workspace_id, datasource_id),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 工作空间-MCP服务关联表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workspace_mcp_servers (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    workspace_id    BIGINT NOT NULL,
    mcp_server_id   BIGINT NOT NULL,
    alias           VARCHAR(100) DEFAULT '', -- 在此工作空间内的别名
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ws_mcp (workspace_id, mcp_server_id),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 工作空间-Agent关联表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workspace_agents (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    workspace_id    BIGINT NOT NULL,
    agent_name      VARCHAR(100) NOT NULL,   -- 关联 adh_agents.name
    is_enabled      TINYINT DEFAULT 1,
    config_override JSON,                    -- 覆盖Agent默认配置
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ws_agent (workspace_id, agent_name),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 修改对话表增加 workspace_id
-- ============================================================================
ALTER TABLE adh_conversations ADD COLUMN workspace_id BIGINT DEFAULT 0 AFTER datasource_id;
ALTER TABLE adh_conversations ADD INDEX idx_workspace (workspace_id);

-- ============================================================================
-- 数据迁移：为现有用户创建默认工作空间
-- ============================================================================

-- 1. 为每个用户创建默认工作空间
INSERT INTO adh_workspaces (name, description, workspace_type, user_id, is_default, icon, color)
SELECT
    CONCAT(u.username, '的默认工作空间'),
    '系统自动创建的默认工作空间',
    'custom',
    u.id,
    1,
    '🏠',
    '#1890ff'
FROM adh_users u
WHERE NOT EXISTS (
    SELECT 1 FROM adh_workspaces w WHERE w.user_id = u.id AND w.is_default = 1
);

-- 2. 将现有数据源关联到默认工作空间
INSERT INTO adh_workspace_datasources (workspace_id, datasource_id, is_primary)
SELECT
    w.id,
    d.id,
    d.is_default
FROM adh_workspaces w
JOIN adh_datasources d ON d.owner_id = w.user_id
WHERE w.is_default = 1
AND NOT EXISTS (
    SELECT 1 FROM adh_workspace_datasources wd
    WHERE wd.workspace_id = w.id AND wd.datasource_id = d.id
);

-- 3. 将现有MCP服务关联到默认工作空间（公开MCP服务）
INSERT INTO adh_workspace_mcp_servers (workspace_id, mcp_server_id)
SELECT
    w.id,
    m.id
FROM adh_workspaces w
CROSS JOIN adh_mcp_servers m
WHERE w.is_default = 1
AND m.is_active = 1
AND NOT EXISTS (
    SELECT 1 FROM adh_workspace_mcp_servers wm
    WHERE wm.workspace_id = w.id AND wm.mcp_server_id = m.id
);

-- 4. 更新对话记录，关联到默认工作空间
UPDATE adh_conversations c
JOIN adh_workspaces w ON w.user_id = c.user_id AND w.is_default = 1
SET c.workspace_id = w.id
WHERE c.workspace_id = 0;

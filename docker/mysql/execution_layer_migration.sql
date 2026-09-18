-- Execution Layer Migration
-- 多执行层架构:内置执行层 + CLI 执行层(qoder 等)
-- 详见 .claude/plans/execution-layer-design.md

-- 执行层配置表
CREATE TABLE IF NOT EXISTS adh_execution_layers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(200),
    description TEXT,
    layer_type VARCHAR(20) NOT NULL COMMENT 'builtin | cli | docker | remote',
    config JSON COMMENT '类型特定配置,如 {cli_name, cli_path, env, command, timeout}',
    status VARCHAR(20) DEFAULT 'active' COMMENT 'active | inactive | error',
    health_check_at DATETIME,
    last_test_status VARCHAR(20),
    last_test_message TEXT,
    capabilities JSON NULL COMMENT '能力标签列表,如 ["nl2sql","code","mcp"]',
    tools JSON NULL COMMENT '自注册上报的工具/能力目录快照',
    endpoint_url VARCHAR(500) NULL COMMENT '远程执行层可达地址(remote 类型)',
    source VARCHAR(20) NOT NULL DEFAULT 'manual' COMMENT 'manual = 手工配置 | self = SDK 自注册',
    last_heartbeat_at DATETIME NULL COMMENT '最近一次心跳时间',
    registered_at DATETIME NULL COMMENT '首次自注册时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_name (name),
    INDEX idx_layer_type (layer_type),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 工作空间执行层关联表
CREATE TABLE IF NOT EXISTS adh_workspace_execution_layers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    workspace_id INT NOT NULL,
    execution_layer_id INT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    priority INT DEFAULT 0,
    allowed_tools JSON NULL COMMENT '允许使用的工具白名单(JSON 数组),空表示不限制',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_ws_layer (workspace_id, execution_layer_id),
    INDEX idx_workspace (workspace_id),
    INDEX idx_layer (execution_layer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 内置执行层(默认,包装现有 Agent 体系)
INSERT IGNORE INTO adh_execution_layers (name, display_name, description, layer_type, config, status)
VALUES ('builtin', '内置执行层', '平台内置 Agent 执行引擎(SQL/数据分析/可配置 Agent)', 'builtin', '{}', 'active');

-- Qoder Agent SDK 执行层(qoder-agent-sdk,mode=sdk;PAT 由进程环境变量 QODER_PERSONAL_ACCESS_TOKEN 提供,不落库)
-- 统一语义层下取数走声明式 semantic 工具 run_semantic_query(execute_sql 已下线)
INSERT IGNORE INTO adh_execution_layers (name, display_name, description, layer_type, config, status)
VALUES (
    'cli-qoder',
    'qoder CLI',
    'Qoder 官方 Agent SDK(qoder-agent-sdk):子代理/会话恢复/上下文压缩,平台工具(元数据/语义)经进程内 MCP 注入;取数统一走 run_semantic_query',
    'cli',
    JSON_OBJECT('mode', 'sdk', 'cli_name', 'qoder', 'cli_path', 'qodercli', 'sdk_tools', 'all'),
    'active'
);

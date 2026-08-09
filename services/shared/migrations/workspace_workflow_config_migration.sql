-- Workspace Workflow Configuration Migration
-- Allows workspaces to have their own workflow configuration

-- Workspace workflow config table
CREATE TABLE IF NOT EXISTS adh_workspace_workflow_configs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    workspace_id BIGINT NOT NULL UNIQUE,
    workflow_template_id BIGINT,  -- references adh_workflow_configs.id
    pipeline_mode VARCHAR(20) DEFAULT 'agent',  -- quick, deep, agent
    retrieval_strategy VARCHAR(50) DEFAULT 'hybrid',  -- hybrid, full_table, column_first, two_stage, bidirectional
    max_iterations INT DEFAULT 10,
    config_json JSON,  -- additional custom config
    is_active TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_template (workflow_template_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

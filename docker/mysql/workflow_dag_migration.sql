-- ============================================================================
-- DAG 工作流编排系统 - 数据库迁移脚本
-- ============================================================================

-- 1. 扩展 adh_workflow_steps 表，添加 DAG 相关字段
ALTER TABLE adh_workflow_steps ADD COLUMN position_x FLOAT DEFAULT 0;
ALTER TABLE adh_workflow_steps ADD COLUMN position_y FLOAT DEFAULT 0;
ALTER TABLE adh_workflow_steps ADD COLUMN dependencies TEXT COMMENT 'JSON array of step_ids that this node depends on';
ALTER TABLE adh_workflow_steps ADD COLUMN node_type VARCHAR(50) DEFAULT 'step' COMMENT 'Node type: start/end/step/condition/parallel/merge/agent/mcp_tool';

-- 2. 创建工作流边表（用于存储节点之间的连接关系）
CREATE TABLE IF NOT EXISTS adh_workflow_edges (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    workflow_id         BIGINT NOT NULL,
    source_step_id      BIGINT NOT NULL,
    target_step_id      BIGINT NOT NULL,
    edge_type           VARCHAR(50) DEFAULT 'normal' COMMENT 'Edge type: normal/conditional/error',
    condition_expr      TEXT COMMENT 'Condition expression for conditional edges',
    label               VARCHAR(100) COMMENT 'Edge label for display',
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_workflow (workflow_id),
    INDEX idx_source (source_step_id),
    INDEX idx_target (target_step_id)
) ENGINE=InnoDB;

-- 3. 扩展 adh_workflow_configs 表，添加 DAG 配置字段
ALTER TABLE adh_workflow_configs ADD COLUMN dag_config TEXT COMMENT 'JSON config for DAG layout and settings';
ALTER TABLE adh_workflow_configs ADD COLUMN workflow_type VARCHAR(50) DEFAULT 'linear' COMMENT 'Workflow type: linear/dag';

-- 4. 扩展 adh_workflow_logs 表，添加 DAG 执行信息
ALTER TABLE adh_workflow_logs ADD COLUMN execution_dag TEXT COMMENT 'JSON snapshot of DAG structure at execution time';
ALTER TABLE adh_workflow_logs ADD COLUMN node_status TEXT COMMENT 'JSON map of node_id -> status for DAG execution tracking';

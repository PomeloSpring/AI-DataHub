-- Execution Layer Registry enhancement — self-registration & discovery
-- Part of Phase 3 (Execution-layer registration and discovery)
-- Adds capability / tool / heartbeat / provenance columns to adh_execution_layers.

ALTER TABLE adh_execution_layers ADD COLUMN IF NOT EXISTS capabilities JSON NULL COMMENT '能力标签列表,如 ["nl2sql","code","mcp"]';
ALTER TABLE adh_execution_layers ADD COLUMN IF NOT EXISTS tools JSON NULL COMMENT '自注册上报的工具/能力目录快照';
ALTER TABLE adh_execution_layers ADD COLUMN IF NOT EXISTS endpoint_url VARCHAR(500) NULL COMMENT '远程执行层可达地址(remote 类型)';
ALTER TABLE adh_execution_layers ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'manual' COMMENT 'manual = 手工配置 | self = SDK 自注册';
ALTER TABLE adh_execution_layers ADD COLUMN IF NOT EXISTS last_heartbeat_at DATETIME NULL COMMENT '最近一次心跳时间';
ALTER TABLE adh_execution_layers ADD COLUMN IF NOT EXISTS registered_at DATETIME NULL COMMENT '首次自注册时间';

-- Seed the built-in layer provenance (already inserted by execution_layer_migration.sql)
UPDATE adh_execution_layers SET source = 'manual' WHERE source IS NULL;

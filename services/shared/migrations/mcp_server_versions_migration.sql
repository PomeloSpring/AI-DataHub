-- MCP Server Versions table — tracks configuration snapshots for rollback
-- Created as part of Phase 2 (Dynamic Configuration + Version Management)

CREATE TABLE IF NOT EXISTS adh_mcp_server_versions (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    config_id           BIGINT NOT NULL COMMENT 'FK to adh_mcp_servers.id',
    config_key          VARCHAR(200) NOT NULL COMMENT 'MCP server name',
    version             INT NOT NULL DEFAULT 1,
    content             JSON NOT NULL COMMENT 'Full config snapshot (transport, url, command, args, env, tools_config, description)',
    change_log          TEXT,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by          VARCHAR(100),
    is_current          TINYINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    INDEX idx_config_id (config_id),
    INDEX idx_config_key (config_key)
) ENGINE=InnoDB;

-- Add version + created_by columns to adh_mcp_servers if not exists
ALTER TABLE adh_mcp_servers ADD COLUMN IF NOT EXISTS version INT NOT NULL DEFAULT 1;
ALTER TABLE adh_mcp_servers ADD COLUMN IF NOT EXISTS created_by VARCHAR(100);

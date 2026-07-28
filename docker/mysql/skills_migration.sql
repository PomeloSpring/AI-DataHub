-- Skills table: stores analysis skills (prompt templates)
-- System skills are loaded from config/skills/ files; user-created skills are stored here.
CREATE TABLE IF NOT EXISTS adh_skills (
    id BIGINT NOT NULL AUTO_INCREMENT,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(100) DEFAULT '',
    description VARCHAR(500) DEFAULT '',
    category VARCHAR(50) DEFAULT 'analysis',
    system_prompt TEXT,
    skill_config TEXT,
    source_type VARCHAR(20) DEFAULT 'user' COMMENT 'system = file-based, user = user-created',
    source_skill VARCHAR(100) DEFAULT '' COMMENT 'copied from which system skill',
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Remove the 6 analysis agents from adh_agents (they are now skills)
DELETE FROM adh_agents WHERE name IN ('traffic', 'user_profiling', 'funnel', 'retention', 'anomaly', 'trend');

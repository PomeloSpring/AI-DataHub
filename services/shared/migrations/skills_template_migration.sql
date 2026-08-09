-- Skills Template Migration
-- Replaces adh_prompts with a more comprehensive skills system

-- Skills template table
CREATE TABLE IF NOT EXISTS adh_skill_templates (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    skill_key VARCHAR(100) NOT NULL UNIQUE,
    skill_name VARCHAR(200) NOT NULL,
    description TEXT,
    category VARCHAR(50) DEFAULT 'custom',  -- nl2sql, analysis, chart, correction, prediction, custom
    system_prompt LONGTEXT,
    skill_config JSON,       -- yaml config as JSON
    tools_json JSON,         -- tool definitions
    examples_json JSON,      -- examples
    version INT DEFAULT 1,
    is_active TINYINT DEFAULT 1,
    workspace_id BIGINT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by VARCHAR(100),
    INDEX idx_skill_key (skill_key),
    INDEX idx_category (category),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Skills version history
CREATE TABLE IF NOT EXISTS adh_skill_template_versions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    skill_id BIGINT NOT NULL,
    skill_key VARCHAR(100) NOT NULL,
    version INT NOT NULL,
    system_prompt LONGTEXT,
    skill_config JSON,
    tools_json JSON,
    examples_json JSON,
    change_log TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(100),
    is_current TINYINT DEFAULT 0,
    INDEX idx_skill_id (skill_id),
    INDEX idx_skill_key (skill_key),
    FOREIGN KEY (skill_id) REFERENCES adh_skill_templates(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Skills scripts table
CREATE TABLE IF NOT EXISTS adh_skill_scripts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    skill_id BIGINT NOT NULL,
    script_name VARCHAR(200) NOT NULL,
    script_type VARCHAR(20) DEFAULT 'python',  -- python, shell, javascript
    script_content LONGTEXT,
    file_path VARCHAR(500),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_skill_id (skill_id),
    FOREIGN KEY (skill_id) REFERENCES adh_skill_templates(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Migrate existing prompts to skills (if adh_prompts exists)
INSERT IGNORE INTO adh_skill_templates (skill_key, skill_name, description, category, system_prompt, version, is_active, created_by)
SELECT
    prompt_key,
    prompt_name,
    COALESCE(description, ''),
    CASE
        WHEN prompt_key LIKE '%metadata%' THEN 'nl2sql'
        WHEN prompt_key LIKE '%sql%' THEN 'nl2sql'
        WHEN prompt_key LIKE '%analysis%' THEN 'analysis'
        WHEN prompt_key LIKE '%chart%' THEN 'chart'
        WHEN prompt_key LIKE '%correction%' THEN 'correction'
        ELSE 'custom'
    END,
    system_prompt,
    version,
    is_active,
    created_by
FROM adh_prompts
WHERE EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'adh_prompts');

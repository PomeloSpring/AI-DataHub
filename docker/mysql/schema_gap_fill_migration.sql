-- ============================================================================
-- 存量库补齐迁移（gap-fill）：把"代码已按列名读写、但远端元库尚未落库"的字段一次补齐
--
-- 背景：仓库里的 *_migration.sql 多数带 `USE adh;`，而实际部署的元库是 services/.env 里的
--       METADATA_DB_DATABASE（现为 adh2），这些脚本在目标库上直接失败 → 代码与 schema 长期漂移，
--       表现为 pymysql 1054 "Unknown column ..."（如 chat 会话清单的 waker_key）。
-- 约定：本脚本 **不含 USE**，用 `mysql ... <METADATA_DB_DATABASE> < 本文件` 施加到任意元库；
--       全部 DDL 幂等（information_schema 守卫），可重复执行；已存在的列/表/索引自动跳过。
-- 与既有特性脚本的关系：语义等价的单一来源仍是
--       trusted_execution_migration.sql / semantics_dialect_migration.sql /
--       chat_attachments_object_storage_migration.sql / conversation_waker_key_migration.sql /
--       alias_suggestions_migration.sql / services/shared/migrations/skills_template_migration.sql，
--       本脚本只是"绕过 USE 阻碍"的一次性补齐入口，两边重复执行都安全。
-- 不猜历史：新增列一律 NULL 或保守默认（报告 generation_status='legacy'、
--       publication_status='draft'、share_revoked=1），不把存量数据自动变成可用/可见。
-- ============================================================================

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_gap_add_column$$
CREATE PROCEDURE adh_gap_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema=DATABASE() AND table_name=tbl)
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema=DATABASE() AND table_name=tbl AND column_name=col) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DROP PROCEDURE IF EXISTS adh_gap_add_index$$
CREATE PROCEDURE adh_gap_add_index(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN definition TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema=DATABASE() AND table_name=tbl)
       AND NOT EXISTS (SELECT 1 FROM information_schema.statistics
               WHERE table_schema=DATABASE() AND table_name=tbl AND index_name=idx) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

-- ----------------------------------------------------------------------------
-- 1. 可信执行 / 报告与定时任务（dataviz.report_service、dataflow.scheduled_task_service）
--    缺列后果：创建报告 / 认领日志 / 幂等 run_key 直接 1054
-- ----------------------------------------------------------------------------
CALL adh_gap_add_column('adh_scheduled_logs', 'run_key', 'VARCHAR(100) NULL');
CALL adh_gap_add_column('adh_scheduled_logs', 'lease_expires_at', 'DATETIME NULL');
CALL adh_gap_add_column('adh_scheduled_logs', 'stage_error_code', 'VARCHAR(64) NULL');
CALL adh_gap_add_index('adh_scheduled_logs', 'uk_scheduled_run_key', 'UNIQUE KEY uk_scheduled_run_key (run_key)');
CALL adh_gap_add_index('adh_scheduled_logs', 'idx_scheduled_lease', 'INDEX idx_scheduled_lease (status, lease_expires_at)');

CALL adh_gap_add_column('adh_reports', 'task_id', 'BIGINT NULL');
CALL adh_gap_add_column('adh_reports', 'log_id', 'BIGINT NULL');
CALL adh_gap_add_column('adh_reports', 'view_count', 'BIGINT NOT NULL DEFAULT 0');
CALL adh_gap_add_column('adh_reports', 'generation_status', "VARCHAR(20) NOT NULL DEFAULT 'legacy'");
CALL adh_gap_add_column('adh_reports', 'publication_status', "VARCHAR(20) NOT NULL DEFAULT 'draft'");
CALL adh_gap_add_column('adh_reports', 'security_context', 'JSON NULL');
CALL adh_gap_add_column('adh_reports', 'evidence_summary', 'JSON NULL');
CALL adh_gap_add_column('adh_reports', 'analysis_source', 'JSON NULL');
CALL adh_gap_add_column('adh_reports', 'run_key', 'VARCHAR(100) NULL');
CALL adh_gap_add_column('adh_reports', 'stage_error_code', 'VARCHAR(64) NULL');
CALL adh_gap_add_column('adh_reports', 'lease_expires_at', 'DATETIME NULL');
CALL adh_gap_add_column('adh_reports', 'share_token_hash', 'CHAR(64) NULL');
CALL adh_gap_add_column('adh_reports', 'share_expires_at', 'DATETIME NULL');
CALL adh_gap_add_column('adh_reports', 'share_revoked', 'TINYINT NOT NULL DEFAULT 1');
CALL adh_gap_add_index('adh_reports', 'uk_report_run_key', 'UNIQUE KEY uk_report_run_key (run_key)');
CALL adh_gap_add_index('adh_reports', 'idx_report_lease', 'INDEX idx_report_lease (generation_status, lease_expires_at)');
CALL adh_gap_add_index('adh_reports', 'idx_report_log', 'INDEX idx_report_log (log_id)');

-- 保存查询的来源数据源：历史行保持 NULL，生成报告时要求补建模，不隐式套用默认引擎
CALL adh_gap_add_column('adh_saved_queries', 'datasource_id', 'BIGINT NULL');
CALL adh_gap_add_column('adh_saved_queries', 'workspace_id', 'BIGINT NULL');

-- ----------------------------------------------------------------------------
-- 2. Chat 附件对象存储（datamind.multimodal：storage_type 区分 local / object）
-- ----------------------------------------------------------------------------
CALL adh_gap_add_column('adh_chat_attachments', 'storage_type',
                        "VARCHAR(16) NOT NULL DEFAULT 'local' COMMENT 'local | object'");

-- ----------------------------------------------------------------------------
-- 3. 语义层多方言编译 + SQL 模板绑定（shared/semantics planner、binding_resolver）
-- ----------------------------------------------------------------------------
CALL adh_gap_add_column('adh_metrics', 'formula_dialects',
                        "JSON NULL COMMENT '按方言注册的可下推表达式 {\"mysql\":...,\"postgres\":null}; 键存在值为 null=显式不支持该方言'");
CALL adh_gap_add_column('adh_ontology_bindings', 'template_ref',
                        "VARCHAR(64) NULL COMMENT 'bind_kind=sql_template 时指向 adh_sql_templates.template_id'");
CALL adh_gap_add_column('adh_sql_templates', 'dialect',
                        "VARCHAR(16) NOT NULL DEFAULT '' COMMENT 'mysql|postgres|generic; 空视同 mysql'");
CALL adh_gap_add_column('adh_datasources', 'ssl_mode',
                        "VARCHAR(16) NOT NULL DEFAULT 'disabled' COMMENT 'TLS 模式: disabled|prefer|require|disable(PG语义)'");

-- bind_kind ENUM 扩值集（追加 sql_template；MODIFY 幂等，存量值不受影响）
SET @has_sqltpl := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_ontology_bindings'
      AND COLUMN_NAME = 'bind_kind'
      AND COLUMN_TYPE LIKE '%sql_template%'
);
SET @ddl := IF(@has_sqltpl = 0,
    'ALTER TABLE adh_ontology_bindings MODIFY COLUMN bind_kind ENUM(''primary'',''lateral'',''bridge'',''metric_source'',''sql_template'') NOT NULL DEFAULT ''primary''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 4. AS-BOT 会话隔离 + 别名回流队列（若已单独执行过对应脚本，此处自动跳过）
-- ----------------------------------------------------------------------------
CALL adh_gap_add_column('adh_conversations', 'waker_key', "VARCHAR(64) NOT NULL DEFAULT ''");
CALL adh_gap_add_index('adh_conversations', 'idx_user_waker_updated',
                       'INDEX idx_user_waker_updated (user_id, waker_key, updated_at)');

-- ----------------------------------------------------------------------------
-- 5. 工作空间类型（agent_pipeline 读 workspace_type 注入 prompt；缺列时静默按 custom）
-- ----------------------------------------------------------------------------
CALL adh_gap_add_column('adh_workspaces', 'workspace_type',
                        "VARCHAR(32) DEFAULT 'custom' COMMENT 'data_analysis, log_analysis, ops, custom'");

-- ----------------------------------------------------------------------------
-- 6. AS-BOT 审批状态机：status ENUM 缺 'executing' 时，wakers.py 的执行领取
--    （UPDATE ... SET status='executing'）会被 MySQL 判为 1265 Data truncated，
--    审批通过但执行不落地。值集只追加，存量行不受影响（MODIFY 幂等）。
-- ----------------------------------------------------------------------------
SET @has_executing := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_as_bot_approvals'
      AND COLUMN_NAME = 'status'
      AND COLUMN_TYPE LIKE '%executing%'
);
SET @ddl := IF(@has_executing = 0,
    'ALTER TABLE adh_as_bot_approvals MODIFY COLUMN status ENUM(''pending'',''approved'',''executing'',''rejected'',''executed'',''failed'') DEFAULT ''pending'', MODIFY COLUMN user_id BIGINT NOT NULL, MODIFY COLUMN decided_by BIGINT NULL',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 7. aiplatform /api/admin/skills 的三张表（DDL 与 services/shared/migrations/skills_template_migration.sql 一致）
--    缺表后果：技能管理相关接口全部 500（Table 'adh2.adh_skill_templates' doesn't exist）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_skill_templates (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    skill_key VARCHAR(100) NOT NULL UNIQUE,
    skill_name VARCHAR(200) NOT NULL,
    description TEXT,
    category VARCHAR(50) DEFAULT 'custom',
    system_prompt LONGTEXT,
    skill_config JSON,
    tools_json JSON,
    examples_json JSON,
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

CREATE TABLE IF NOT EXISTS adh_skill_scripts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    skill_id BIGINT NOT NULL,
    script_name VARCHAR(200) NOT NULL,
    script_type VARCHAR(20) DEFAULT 'python',
    script_content LONGTEXT,
    file_path VARCHAR(500),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_skill_id (skill_id),
    FOREIGN KEY (skill_id) REFERENCES adh_skill_templates(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ----------------------------------------------------------------------------
-- 8. 审计（不落 DDL）：确认补齐结果，缺失项应为空
-- ----------------------------------------------------------------------------
SELECT t.tbl, t.col,
       IF(c.COLUMN_NAME IS NULL, 'MISSING', 'OK') AS state
FROM (
    SELECT 'adh_reports' tbl, 'run_key' col UNION ALL
    SELECT 'adh_reports', 'generation_status' UNION ALL
    SELECT 'adh_reports', 'publication_status' UNION ALL
    SELECT 'adh_reports', 'security_context' UNION ALL
    SELECT 'adh_reports', 'evidence_summary' UNION ALL
    SELECT 'adh_reports', 'analysis_source' UNION ALL
    SELECT 'adh_reports', 'stage_error_code' UNION ALL
    SELECT 'adh_reports', 'lease_expires_at' UNION ALL
    SELECT 'adh_reports', 'share_token_hash' UNION ALL
    SELECT 'adh_reports', 'share_expires_at' UNION ALL
    SELECT 'adh_reports', 'share_revoked' UNION ALL
    SELECT 'adh_scheduled_logs', 'run_key' UNION ALL
    SELECT 'adh_scheduled_logs', 'lease_expires_at' UNION ALL
    SELECT 'adh_scheduled_logs', 'stage_error_code' UNION ALL
    SELECT 'adh_saved_queries', 'datasource_id' UNION ALL
    SELECT 'adh_chat_attachments', 'storage_type' UNION ALL
    SELECT 'adh_metrics', 'formula_dialects' UNION ALL
    SELECT 'adh_ontology_bindings', 'template_ref' UNION ALL
    SELECT 'adh_sql_templates', 'dialect' UNION ALL
    SELECT 'adh_datasources', 'ssl_mode' UNION ALL
    SELECT 'adh_conversations', 'waker_key' UNION ALL
    SELECT 'adh_workspaces', 'workspace_type'
) t
LEFT JOIN information_schema.COLUMNS c
       ON c.TABLE_SCHEMA = DATABASE() AND c.TABLE_NAME = t.tbl AND c.COLUMN_NAME = t.col;

-- ENUM 值集校验（列存在但值集缺 'executing' 同样是缺陷，上一段查询看不见）
SELECT 'adh_as_bot_approvals.status' AS check_item,
       IF(COLUMN_TYPE LIKE '%executing%', 'OK', CONCAT('ENUM_MISSING: ', COLUMN_TYPE)) AS state
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_as_bot_approvals' AND COLUMN_NAME = 'status';

SELECT want.table_name, 'TABLE_MISSING' AS state
FROM (
    SELECT 'adh_skill_templates' table_name UNION ALL
    SELECT 'adh_skill_template_versions' UNION ALL
    SELECT 'adh_skill_scripts' UNION ALL
    SELECT 'adh_alias_suggestions'
) want
LEFT JOIN information_schema.tables i
       ON i.TABLE_SCHEMA = DATABASE() AND i.TABLE_NAME = want.table_name
WHERE i.TABLE_NAME IS NULL;

DROP PROCEDURE adh_gap_add_column;
DROP PROCEDURE adh_gap_add_index;

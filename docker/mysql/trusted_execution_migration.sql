-- 可信分析 M1/M2 增量迁移。执行前备份；仅依赖已部署的定时任务、审批、保存查询表。
-- 可重复执行，不导入演示数据，不猜测历史任务归属，不将历史报告自动公开。
CREATE TABLE IF NOT EXISTS adh_reports (
    id BIGINT PRIMARY KEY,
    task_id BIGINT NULL,
    log_id BIGINT NULL,
    title VARCHAR(512) NOT NULL,
    content LONGTEXT,
    format VARCHAR(20) NOT NULL DEFAULT 'markdown',
    access_mode VARCHAR(20) NOT NULL DEFAULT 'private',
    access_token VARCHAR(255) NULL,
    workspace_id BIGINT NOT NULL DEFAULT 0,
    owner_id BIGINT NOT NULL DEFAULT 0,
    view_count BIGINT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_ta_add_column$$
CREATE PROCEDURE adh_ta_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema=DATABASE() AND table_name=tbl AND column_name=col) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DROP PROCEDURE IF EXISTS adh_ta_add_index$$
CREATE PROCEDURE adh_ta_add_index(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN definition TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.statistics
                   WHERE table_schema=DATABASE() AND table_name=tbl AND index_name=idx) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

CALL adh_ta_add_column('adh_scheduled_logs', 'run_key', 'VARCHAR(100) NULL');
CALL adh_ta_add_column('adh_scheduled_logs', 'lease_expires_at', 'DATETIME NULL');
CALL adh_ta_add_column('adh_scheduled_logs', 'stage_error_code', 'VARCHAR(64) NULL');
CALL adh_ta_add_index('adh_scheduled_logs', 'uk_scheduled_run_key', 'UNIQUE KEY uk_scheduled_run_key (run_key)');
CALL adh_ta_add_index('adh_scheduled_logs', 'idx_scheduled_lease', 'INDEX idx_scheduled_lease (status, lease_expires_at)');

CALL adh_ta_add_column('adh_reports', 'task_id', 'BIGINT NULL');
CALL adh_ta_add_column('adh_reports', 'log_id', 'BIGINT NULL');
CALL adh_ta_add_column('adh_reports', 'view_count', 'BIGINT NOT NULL DEFAULT 0');
CALL adh_ta_add_column('adh_reports', 'generation_status', 'VARCHAR(20) NOT NULL DEFAULT ''legacy''');
CALL adh_ta_add_column('adh_reports', 'publication_status', 'VARCHAR(20) NOT NULL DEFAULT ''draft''');
CALL adh_ta_add_column('adh_reports', 'security_context', 'JSON NULL');
CALL adh_ta_add_column('adh_reports', 'evidence_summary', 'JSON NULL');
CALL adh_ta_add_column('adh_reports', 'analysis_source', 'JSON NULL');
CALL adh_ta_add_column('adh_reports', 'run_key', 'VARCHAR(100) NULL');
CALL adh_ta_add_column('adh_reports', 'stage_error_code', 'VARCHAR(64) NULL');
CALL adh_ta_add_column('adh_reports', 'lease_expires_at', 'DATETIME NULL');
CALL adh_ta_add_column('adh_reports', 'share_token_hash', 'CHAR(64) NULL');
CALL adh_ta_add_column('adh_reports', 'share_expires_at', 'DATETIME NULL');
CALL adh_ta_add_column('adh_reports', 'share_revoked', 'TINYINT NOT NULL DEFAULT 1');
CALL adh_ta_add_index('adh_reports', 'uk_report_run_key', 'UNIQUE KEY uk_report_run_key (run_key)');
CALL adh_ta_add_index('adh_reports', 'idx_report_lease', 'INDEX idx_report_lease (generation_status, lease_expires_at)');
CALL adh_ta_add_index('adh_reports', 'idx_report_log', 'INDEX idx_report_log (log_id)');

-- 历史保存查询缺少明确来源时保留 NULL，生成报告时要求补建模，不能隐式使用默认引擎。
CALL adh_ta_add_column('adh_saved_queries', 'datasource_id', 'BIGINT NULL');
CALL adh_ta_add_column('adh_saved_queries', 'workspace_id', 'BIGINT NULL');
ALTER TABLE adh_as_bot_approvals
    MODIFY COLUMN status ENUM('pending','approved','executing','rejected','executed','failed') DEFAULT 'pending',
    MODIFY COLUMN user_id BIGINT NOT NULL,
    MODIFY COLUMN decided_by BIGINT NULL;

DROP PROCEDURE adh_ta_add_column;
DROP PROCEDURE adh_ta_add_index;

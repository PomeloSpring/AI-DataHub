-- ═══════════════════════════════════════════════════════════════
-- 同步任务表结构对齐（修复 CREATE IF NOT EXISTS 撞旧表导致的静默跳过）
--
-- 背景：sync_dag_migration.sql 用 CREATE TABLE IF NOT EXISTS 定义 adh_sync_tasks /
-- adh_sync_logs 的新结构（含 timezone / incremental_column / dag_id / task_config 等），
-- 但运行时元库存在**同名旧结构表**，IF NOT EXISTS 静默跳过 → 新代码查询 1054
-- （beat: Unknown column 'timezone' → 整个周期调度加载失败）。
--
-- 本迁移把两张表重建为 sync_dag_migration.sql 的权威结构；
-- **有数据时直接报错终止（SIGNAL），绝不静默跳过或误删**——先人工迁移数据再执行。
-- 可重复执行：表已是新结构时 guard 通过、DROP/CREATE 幂等。
--
-- 执行: mysql -u root -p < sync_dag_schema_realign_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh2;

-- ── 守卫：两表非空即中止（fail-loud，禁止无损外观掩盖结构错误）；表不存在则直接放行 ──
DROP PROCEDURE IF EXISTS adh_sync_realign_guard;
DELIMITER //
CREATE PROCEDURE adh_sync_realign_guard()
BEGIN
    DECLARE n INT;
    SELECT COUNT(*) INTO n FROM information_schema.tables
      WHERE table_schema = 'adh2' AND table_name = 'adh_sync_tasks';
    IF n > 0 THEN
        SELECT COUNT(*) INTO n FROM adh_sync_tasks;
        IF n > 0 THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'adh_sync_tasks 非空：请先迁移数据再执行本结构对齐迁移';
        END IF;
    END IF;
    SELECT COUNT(*) INTO n FROM information_schema.tables
      WHERE table_schema = 'adh2' AND table_name = 'adh_sync_logs';
    IF n > 0 THEN
        SELECT COUNT(*) INTO n FROM adh_sync_logs;
        IF n > 0 THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'adh_sync_logs 非空：请先迁移数据再执行本结构对齐迁移';
        END IF;
    END IF;
END//
DELIMITER ;
CALL adh_sync_realign_guard();
DROP PROCEDURE adh_sync_realign_guard;

DROP TABLE IF EXISTS adh_sync_tasks;
DROP TABLE IF EXISTS adh_sync_logs;

-- ── 权威结构（与 docker/mysql/sync_dag_migration.sql 保持一致）─────────
CREATE TABLE IF NOT EXISTS adh_sync_tasks (
    id BIGINT PRIMARY KEY,
    name VARCHAR(200) NOT NULL COMMENT '任务名称',
    description TEXT COMMENT '任务描述',
    source_type VARCHAR(32) NOT NULL COMMENT '来源类型: mysql / postgres / doris（随数据源 db_type）',
    source_config JSON COMMENT '兼容旧结构的补充配置（筛选/批大小等，不含连接凭据）',
    target_type VARCHAR(32) NOT NULL COMMENT '目标类型: mysql / doris / postgres',
    target_config JSON COMMENT '兼容旧结构的补充配置（写入模式等，不含连接凭据）',
    source_datasource_name VARCHAR(128) COMMENT '源数据源名称（adh_datasources.name）',
    source_table VARCHAR(256) COMMENT '源表名',
    target_datasource_name VARCHAR(128) COMMENT '目标数据源名称（adh_datasources.name）',
    target_table VARCHAR(256) COMMENT '目标表名',
    sync_mode VARCHAR(20) NOT NULL DEFAULT 'full' COMMENT '同步模式: full / incremental',
    incremental_column VARCHAR(128) COMMENT '增量列（sync_mode=incremental 时必填）',
    schedule_cron VARCHAR(50) COMMENT 'Cron 表达式（NULL=仅手动触发）',
    timezone VARCHAR(50) DEFAULT 'Asia/Shanghai' COMMENT '调度时区',
    dag_id VARCHAR(200) COMMENT '关联 DAG 标识（预留）',
    task_config JSON COMMENT '任务配置（批大小、超时等）',
    is_active TINYINT DEFAULT 1 COMMENT '是否启用',
    status VARCHAR(20) DEFAULT 'idle' COMMENT '任务状态: idle / running / error',
    workspace_id BIGINT DEFAULT 0 COMMENT '所属工作空间',
    owner_id BIGINT DEFAULT 0 COMMENT '创建人（0=未认领，未认领不参与调度）',
    last_run_at DATETIME COMMENT '上次运行时间',
    last_status VARCHAR(20) COMMENT '上次状态: success / failed / partial / timeout / cancelled',
    run_count INT DEFAULT 0 COMMENT '累计运行次数',
    timeout_seconds INT DEFAULT 1800 COMMENT '单次执行超时（秒）',
    max_retries INT DEFAULT 0 COMMENT '失败重试次数（0=不重试）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_sync_task_name (workspace_id, name),
    INDEX idx_sync_owner (owner_id),
    INDEX idx_sync_active (is_active),
    INDEX idx_sync_cron (is_active, schedule_cron)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据同步任务定义';

CREATE TABLE IF NOT EXISTS adh_sync_logs (
    id BIGINT PRIMARY KEY,
    sync_task_id BIGINT NOT NULL COMMENT '关联同步任务 ID（adh_sync_tasks.id）',
    workspace_id BIGINT DEFAULT 0 COMMENT '冗余 workspace_id',
    dag_run_id VARCHAR(100) COMMENT '所属 DAG 运行键（单任务形态为空）',
    node_run_id BIGINT DEFAULT 0 COMMENT '所属 DAG 节点实例 ID（单任务形态为 0）',
    status VARCHAR(20) NOT NULL COMMENT '状态: queued / running / success / failed / partial / timeout / cancelled',
    trigger_type VARCHAR(20) NOT NULL COMMENT '触发方式: cron / manual / retry',
    rows_read BIGINT DEFAULT 0 COMMENT '读取行数',
    rows_written BIGINT DEFAULT 0 COMMENT '写入行数',
    rows_failed BIGINT DEFAULT 0 COMMENT '失败行数',
    records_synced BIGINT DEFAULT 0 COMMENT '累计同步行数（兼容列）',
    elapsed_ms INT COMMENT '执行耗时（毫秒）',
    error_code VARCHAR(50) COMMENT '错误码（对外可诊断，不含原始报错）',
    error_message TEXT COMMENT '错误信息（已脱敏）',
    worker_id VARCHAR(64) COMMENT '执行 Worker 标识',
    lease_expires_at DATETIME COMMENT '租约到期时间（超时清理依据）',
    started_at DATETIME NOT NULL COMMENT '开始时间',
    finished_at DATETIME COMMENT '结束时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sync_log_task (sync_task_id),
    INDEX idx_sync_log_status (status),
    INDEX idx_sync_log_started (started_at),
    INDEX idx_sync_log_lease (status, lease_expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据同步执行日志';

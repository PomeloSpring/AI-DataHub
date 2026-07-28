-- ═══════════════════════════════════════════════════════════════
-- 定时任务模块 — 建表 SQL
-- 执行: mysql -u root -p < scheduled_task_migration.sql
-- ═══════════════════════════════════════════════════════════════

-- 通知渠道配置
CREATE TABLE IF NOT EXISTS adh_notification_channels (
    id BIGINT PRIMARY KEY,
    name VARCHAR(100) NOT NULL COMMENT '渠道名称',
    channel_type VARCHAR(20) NOT NULL COMMENT '渠道类型: dingtalk / feishu / wecom / email / webhook',
    config JSON NOT NULL COMMENT '渠道配置（webhook URL、密钥等）',
    is_active TINYINT DEFAULT 1 COMMENT '是否启用',
    workspace_id BIGINT DEFAULT 0 COMMENT '所属工作空间',
    owner_id BIGINT NOT NULL COMMENT '创建人',
    last_test_at DATETIME COMMENT '最后测试时间',
    last_test_status VARCHAR(20) COMMENT '最后测试状态: success / failed',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_type (channel_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='通知渠道配置';

-- 定时任务主表
CREATE TABLE IF NOT EXISTS adh_scheduled_tasks (
    id BIGINT PRIMARY KEY,
    name VARCHAR(200) NOT NULL COMMENT '任务名称',
    description TEXT COMMENT '任务描述',
    task_type VARCHAR(20) NOT NULL COMMENT '执行模式: query / agent',
    task_config JSON NOT NULL COMMENT '任务配置（SQL列表/Agent问题列表/数据源ID等）',
    report_template_key VARCHAR(100) COMMENT '报告模板 key（关联 config/templates/）',
    cron_expression VARCHAR(50) DEFAULT '' COMMENT 'Cron 表达式（webhook 模式可为空）',
    trigger_type VARCHAR(20) DEFAULT 'cron' COMMENT '触发方式: cron / webhook / both',
    webhook_token VARCHAR(64) COMMENT 'Webhook 认证 Token',
    webhook_secret VARCHAR(128) COMMENT 'Webhook HMAC 签名密钥（可选）',
    timezone VARCHAR(50) DEFAULT 'Asia/Shanghai' COMMENT '时区',
    channel_id BIGINT COMMENT '通知渠道 ID（adh_notification_channels.id）',
    notify_on_success TINYINT DEFAULT 1 COMMENT '成功时是否通知',
    notify_on_failure TINYINT DEFAULT 1 COMMENT '失败时是否通知',
    is_active TINYINT DEFAULT 1 COMMENT '是否启用',
    workspace_id BIGINT DEFAULT 0 COMMENT '所属工作空间',
    owner_id BIGINT NOT NULL COMMENT '创建人',
    last_run_at DATETIME COMMENT '上次运行时间',
    last_status VARCHAR(20) COMMENT '上次状态: success / failed / running / timeout',
    last_error TEXT COMMENT '上次错误信息',
    run_count INT DEFAULT 0 COMMENT '累计运行次数',
    timeout_seconds INT DEFAULT 300 COMMENT '单次执行超时（秒）',
    max_retries INT DEFAULT 0 COMMENT '失败重试次数（0=不重试）',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_owner (owner_id),
    INDEX idx_active (is_active),
    UNIQUE INDEX idx_webhook_token (webhook_token)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='定时任务配置';

-- 定时任务执行历史
CREATE TABLE IF NOT EXISTS adh_scheduled_logs (
    id BIGINT PRIMARY KEY,
    scheduled_task_id BIGINT NOT NULL COMMENT '关联的定时任务 ID',
    workspace_id BIGINT DEFAULT 0 COMMENT '冗余 workspace_id，便于直接查询',
    status VARCHAR(20) NOT NULL COMMENT '执行状态: success / failed / running / timeout / cancelled',
    trigger_type VARCHAR(20) NOT NULL COMMENT '触发方式: cron / manual / retry / webhook',
    celery_task_id VARCHAR(100) COMMENT 'Celery 任务 ID',
    result_summary TEXT COMMENT '结果摘要',
    result_data LONGTEXT COMMENT '完整结果数据（JSON）',
    error_message TEXT COMMENT '错误信息',
    questions_executed JSON COMMENT '实际执行的问题/SQL 列表',
    questions_succeeded INT DEFAULT 0 COMMENT '成功执行的问题数',
    questions_failed INT DEFAULT 0 COMMENT '失败的问题数',
    report_content TEXT COMMENT '生成的报告内容（如有模板）',
    channel_response TEXT COMMENT '通知渠道响应',
    notify_status VARCHAR(20) COMMENT '通知状态: sent / failed / skipped',
    elapsed_ms INT COMMENT '执行耗时（毫秒）',
    token_usage JSON COMMENT 'Token 使用量（Agent 模式）',
    worker_id VARCHAR(50) COMMENT '执行 Worker ID',
    started_at DATETIME NOT NULL COMMENT '开始时间',
    finished_at DATETIME COMMENT '结束时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_task_id (scheduled_task_id),
    INDEX idx_workspace (workspace_id),
    INDEX idx_status (status),
    INDEX idx_started_at (started_at),
    INDEX idx_task_status (scheduled_task_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='定时任务执行历史';

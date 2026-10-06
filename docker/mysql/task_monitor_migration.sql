-- ═══════════════════════════════════════════════════════════════
-- 任务监控（定时任务 / 队列任务 / 系统内置任务）— 建表与权限种子
-- 风格对齐 docker/mysql/*_migration.sql；IF NOT EXISTS / INSERT IGNORE 可重复执行。
-- 运行时元库为 adh2（对齐 observability_migration.sql）。
-- 执行: mysql -u root -p < task_monitor_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh2;

-- ============================================================================
-- 系统内置任务注册与运行状态（暂停开关 + 最近运行结果，供任务监控页展示）
-- 注册表事实源是 services/shared/common/system_jobs.py；本表只存运行态与人工暂停开关。
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_system_jobs (
    job_key      VARCHAR(64)  NOT NULL COMMENT '内置任务键(与代码注册表一致)',
    name         VARCHAR(200) NOT NULL COMMENT '任务名称(业务可读名)',
    description  TEXT         COMMENT '任务职责说明',
    schedule_desc VARCHAR(100) NOT NULL DEFAULT '' COMMENT '调度周期描述(如 每 60 秒)',
    owner_service VARCHAR(32) NOT NULL DEFAULT '' COMMENT '承载服务: dataflow / datacatalog',
    is_active    TINYINT      NOT NULL DEFAULT 1 COMMENT '是否启用(0=人工暂停)',
    paused_at    DATETIME     NULL COMMENT '暂停时间',
    paused_by    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '暂停操作者',
    last_run_at  DATETIME     NULL COMMENT '最近一次运行时间',
    last_status  VARCHAR(20)  NOT NULL DEFAULT '' COMMENT '最近一次状态: success / failed / paused / skipped',
    last_result  VARCHAR(500) NOT NULL DEFAULT '' COMMENT '最近一次结果摘要',
    run_count    INT          NOT NULL DEFAULT 0 COMMENT '累计运行次数',
    created_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (job_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统内置任务注册与运行状态';

-- 内置任务种子（名称/描述以代码注册表为准，此处仅初始化）
INSERT IGNORE INTO adh_system_jobs (job_key, name, description, schedule_desc, owner_service) VALUES
('runs_reconcile', '运行对账与卡死清理',
 '对账定时任务执行实例与报表生成：超过租约/超时的卡死任务标记为超时并释放，避免异常任务卡住队列',
 '每 60 秒', 'dataflow'),
('kb_sync_reconcile', '知识库同步对账',
 '周期比对本体模型版本与知识库同步水位线，对漂移/失败的模型重推脱敏语义文档',
 '每 10 分钟', 'datacatalog');

-- ============================================================================
-- 菜单与权限码（权限码注册表驱动菜单可见性与 API 鉴权）
-- 任务监控是系统配置运维能力：写操作按码门控，读按中间件普通读口径。
-- ============================================================================
INSERT IGNORE INTO adh_menu_registry (menu_key, label, section, module, sort) VALUES
('system:task-monitor', '任务监控', '运维管理', 'system', 53);

INSERT IGNORE INTO adh_perm_registry (perm_code, label, module, module_label, api_pattern, api_method, menu_key, sort) VALUES
('task-monitor:manage', '任务监控操作', 'system', '系统配置',
 '/api/task-monitor/*', 'POST,PUT,PATCH,DELETE', 'system:task-monitor', 530);

UPDATE adh_menu_registry mr
  JOIN adh_perm_registry p ON p.menu_key = mr.menu_key
  SET mr.perm_code = p.perm_code
  WHERE p.menu_key <> '' AND (mr.perm_code IS NULL OR mr.perm_code = '');

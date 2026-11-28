-- ============================================================================
-- 看板刷新服务端化：缓存元数据、单看板刷新配置与执行实例（幂等；不写库名）
--
-- 为什么需要它：
--   右上角整板刷新与定时刷新不能再由浏览器 setInterval 驱动；多用户、多标签页
--   会把同一看板同时刷成 N×M 条查询。这里把刷新配置收敛为“一看板一行”，
--   执行实例落库供 Celery 异步消费与任务监控，缓存时间单独记录用于 TTL 防刷。
--
-- 幂等/边界：
--   - 列与索引用 information_schema 守卫，可重复执行；
--   - active_key 仅 queued/running 非空且唯一，同看板跨进程最多一条活跃 run；
--   - 不写业务数据行，不绕过产品写入口；权限只收窄 dashboard:read 的 POST 落点；
--   - 旧 data_cache 不猜测生成时间，data_cache_at 保持 NULL，需一次管理刷新后带 TTL。
-- ============================================================================

-- ── 1) 图表缓存元数据 ───────────────────────────────────────────────────────
SET @ddl = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_charts' AND COLUMN_NAME='data_cache_at') = 0,
    'ALTER TABLE adh_charts ADD COLUMN data_cache_at DATETIME NULL COMMENT ''共享缓存生成时间(UTC 语义由应用统一)'' AFTER data_cache',
    'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_charts' AND COLUMN_NAME='data_cache_key') = 0,
    'ALTER TABLE adh_charts ADD COLUMN data_cache_key VARCHAR(64) NULL COMMENT ''图表配置+默认参数摘要，防止旧缓存串参数'' AFTER data_cache_at',
    'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
    (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_charts' AND COLUMN_NAME='data_cache_scope') = 0,
    'ALTER TABLE adh_charts ADD COLUMN data_cache_scope VARCHAR(64) NULL COMMENT ''生成缓存时的角色/RLS/列策略作用域，跨作用域不得复用'' AFTER data_cache_key',
    'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── 2) 一看板一份刷新配置 ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_dashboard_refresh_configs (
    dashboard_id BIGINT PRIMARY KEY,
    schedule_type VARCHAR(16) NOT NULL DEFAULT 'disabled' COMMENT 'disabled/interval/cron',
    interval_seconds INT NULL COMMENT '固定周期秒数；30/60/300/600/1800/3600/86400/604800/2592000',
    cron_expression VARCHAR(64) NULL COMMENT '5 字段 Cron',
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
    cache_ttl_seconds INT NOT NULL DEFAULT 60 COMMENT '缓存有效期，下限 30 秒',
    is_active TINYINT NOT NULL DEFAULT 0,
    created_by BIGINT NOT NULL COMMENT '执行身份；每次运行实时解析其角色',
    updated_by BIGINT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1 COMMENT '乐观锁版本',
    last_run_at DATETIME NULL,
    last_status VARCHAR(16) NULL,
    last_error_code VARCHAR(32) NULL,
    last_error_message TEXT NULL,
    run_count INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_refresh_active (is_active, schedule_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='看板整板刷新配置(一看板一行)';

-- ── 3) 看板刷新执行实例 ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_dashboard_refresh_runs (
    id BIGINT NOT NULL AUTO_INCREMENT,
    dashboard_id BIGINT NOT NULL,
    config_version BIGINT NOT NULL DEFAULT 0,
    trigger_type VARCHAR(16) NOT NULL COMMENT 'manual/interval/cron',
    run_key VARCHAR(128) NOT NULL COMMENT '派发前生成，重投幂等',
    status VARCHAR(16) NOT NULL DEFAULT 'queued' COMMENT 'queued/running/success/partial/failed/timeout/cancelled',
    active_key VARCHAR(64) NULL COMMENT '仅活跃实例非空；唯一键防同看板并发',
    requested_by BIGINT NULL,
    worker_id VARCHAR(128) NULL,
    lease_expires_at DATETIME NULL,
    queued_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at DATETIME NULL,
    finished_at DATETIME NULL,
    total_charts INT NOT NULL DEFAULT 0,
    succeeded_charts INT NOT NULL DEFAULT 0,
    failed_charts INT NOT NULL DEFAULT 0,
    result_summary TEXT NULL,
    error_code VARCHAR(32) NULL,
    error_message TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_refresh_run_key (run_key),
    UNIQUE KEY uk_refresh_active (active_key),
    INDEX idx_refresh_dashboard (dashboard_id, created_at),
    INDEX idx_refresh_status (status, lease_expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='看板整板刷新执行实例';

-- ── 4) 权限：只读角色可用图表自己的查询按钮，但不可整板刷新 ─────────────────
-- 方法限定条目不再与 api_method 求笛卡尔积；整板刷新/配置仍只命中 dashboard:manage。
UPDATE adh_perm_registry
   SET api_pattern='GET /api/dashboard*,GET /api/dashboards*,POST /api/dashboard/*/charts/*/refresh,POST /api/charts/*/refresh',
       api_method='GET'
 WHERE perm_code='dashboard:read';
UPDATE adh_perm_registry
   SET api_method='POST,PUT,PATCH,DELETE'
 WHERE perm_code='dashboard:manage';

-- 核验（应用后人工复核）：
-- SELECT dashboard_id, schedule_type, interval_seconds, cron_expression, is_active
--   FROM adh_dashboard_refresh_configs;
-- SELECT dashboard_id, status, active_key FROM adh_dashboard_refresh_runs
--  WHERE status IN ('queued','running');
-- SELECT perm_code, api_pattern, api_method FROM adh_perm_registry
--  WHERE perm_code IN ('dashboard:read','dashboard:manage');

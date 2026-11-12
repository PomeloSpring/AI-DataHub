-- ============================================================================
-- 会话上下文用量与压缩 / 排队与繁忙观测迁移 (agent_session_context_migration.sql)
-- ----------------------------------------------------------------------------
-- 目标库: 由命令行/连接指定 (不在脚本内硬编码 USE)，对运行中的 MySQL 执行一次。
-- 内容:
--   1) adh_agent_sessions 增加"上下文占用"列组：进度条的唯一数据源
--      （值来自 SDK get_context_usage()，即 CLI /context 同源，不自造估算口径）
--   2) adh_agent_sessions 增加"压缩次数/时间"列：手动压缩与 CLI autoCompact 的可见计数
--   3) adh_llm_traces 增加 queue_wait_ms / retry_count：排队时长与自动重试的可观测列
-- 幂等: DDL 经 information_schema 守卫，可重复执行。
--
-- 为什么这些列放在会话表而不是每次现测：服务是 web 单进程、执行层客户端**每轮新建**
-- （resume=sdk_session_id），轮间没有活客户端可问。所以占用值只能在"本来就有连接"的
-- 回合内采集后落库，前端读最近一次采集值（从未测量时返回 available=false，不返回 0%）。
-- ============================================================================

-- ── 1) 上下文占用列 ─────────────────────────────────────────────────────────
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_agent_sessions'
      AND COLUMN_NAME = 'context_used_pct') = 0,
  'ALTER TABLE adh_agent_sessions ADD COLUMN context_used_pct DECIMAL(5,2) NULL COMMENT ''上下文占用百分比（CLI /context 同源，0~100）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_agent_sessions'
      AND COLUMN_NAME = 'context_threshold_pct') = 0,
  'ALTER TABLE adh_agent_sessions ADD COLUMN context_threshold_pct DECIMAL(5,2) NULL COMMENT ''CLI autoCompact 阈值百分比（阈值由 CLI 控制，平台只展示）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_agent_sessions'
      AND COLUMN_NAME = 'context_categories') = 0,
  'ALTER TABLE adh_agent_sessions ADD COLUMN context_categories JSON NULL COMMENT ''分类占用 [{type,percentage}]（system_prompt/system_tools/skills/messages/free_space/auto_compact）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_agent_sessions'
      AND COLUMN_NAME = 'context_model') = 0,
  'ALTER TABLE adh_agent_sessions ADD COLUMN context_model VARCHAR(64) NULL COMMENT ''测量时生效模型（明细 tooltip 用）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_agent_sessions'
      AND COLUMN_NAME = 'context_measured_at') = 0,
  'ALTER TABLE adh_agent_sessions ADD COLUMN context_measured_at DATETIME(6) NULL COMMENT ''最近一次采集时间（NULL=从未测量）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 采集失败原因必须落库：否则前端只能显示"未测量"，用户与排障者都分不清
-- "还没跑过一轮"和"测量一直失败"（no-silent-degradation）。
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_agent_sessions'
      AND COLUMN_NAME = 'context_error') = 0,
  'ALTER TABLE adh_agent_sessions ADD COLUMN context_error VARCHAR(255) NULL COMMENT ''最近一次采集/压缩失败原因（成功采集后清空）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── 2) 压缩计数列 ───────────────────────────────────────────────────────────
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_agent_sessions'
      AND COLUMN_NAME = 'compact_count') = 0,
  'ALTER TABLE adh_agent_sessions ADD COLUMN compact_count INT NOT NULL DEFAULT 0 COMMENT ''本会话已压缩次数（手动 /compact 与 CLI autoCompact）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_agent_sessions'
      AND COLUMN_NAME = 'last_compact_at') = 0,
  'ALTER TABLE adh_agent_sessions ADD COLUMN last_compact_at DATETIME(6) NULL COMMENT ''最近一次压缩完成时间''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── 3) 排队 / 重试观测列 ────────────────────────────────────────────────────
-- 排队等待与自动重试次数落回合级 trace：管理员要能看见"慢是慢在排队还是慢在模型"，
-- 否则并发闸门变成又一个无人能观测的黑盒。写入走观测旁路，失败不影响主链路。
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_llm_traces'
      AND COLUMN_NAME = 'queue_wait_ms') = 0,
  'ALTER TABLE adh_llm_traces ADD COLUMN queue_wait_ms INT NULL COMMENT ''并发闸门排队等待（毫秒，NULL=未排队）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_llm_traces'
      AND COLUMN_NAME = 'retry_count') = 0,
  'ALTER TABLE adh_llm_traces ADD COLUMN retry_count TINYINT NULL COMMENT ''提供商繁忙/限流的自动重试次数（0=一次成功）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

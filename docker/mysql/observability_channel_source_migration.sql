-- ============================================================================
-- 通道的来源标注（adh_llm_traces.channel_source）
--
-- 背景：channel 列 2026-11-05 上线，之前的回合该列为 NULL。用判据回填历史行时，
--       必须把"运行时实记"和"事后回填"分开——否则回填值会被当成一手事实继续引用，
--       而回填本身是有判据边界的（判不出的行留在 NULL）。
--
-- 值域：
--   runtime    执行层在 build_options 时登记的事实（唯一权威来源）
--   backfilled 由 backend/scripts/backfill_trace_channel.py 按判据推断
--   NULL       未记录（判据不足，不猜）
--
-- 幂等：ADD COLUMN 先查 information_schema。
-- ============================================================================

SET @c = (SELECT COUNT(*) FROM information_schema.COLUMNS
          WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_llm_traces' AND COLUMN_NAME = 'channel_source');
SET @ddl = IF(@c = 0,
    'ALTER TABLE adh_llm_traces ADD COLUMN channel_source VARCHAR(16) NULL DEFAULT NULL COMMENT ''通道值来源：runtime=执行层实记，backfilled=判据回填，NULL=未记录''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

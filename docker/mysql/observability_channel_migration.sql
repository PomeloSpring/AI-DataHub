-- ============================================================================
-- 可观测按执行层/模型通道区分（adh_llm_traces 加 layer_id / layer_name / channel）
--
-- 背景：执行层不再只有一条 Qoder 通道。AS-BOT 可以绑「Anthropic 协议直连供应商」的层，
--       两条通道的**计量单位不同**：
--         qoder-gateway   → Qoder 按 credit 计量，不回报 token（实测 total_tokens 恒 0）
--         anthropic-direct→ 供应商按 token 结算，没有 credit 概念（CLI 自报 USD 为估算）
--       原表只有 model_ref，两种单位混在同一张概览里：Credit 卡片显示 0、成本卡片跨币种
--       相加，看起来像"直连什么都没记"，实际 spans/tokens 都落了。
--
-- 口径：
--   - `channel` 值域：'qoder-gateway' | 'anthropic-direct'；历史行为 NULL（按 Qoder 口径解释，
--     不做数据回填猜测 —— 历史回合确实都走 Qoder 网关）；
--   - `layer_id/layer_name` 记录**实际生效**的执行层（AS-BOT 绑定或默认层解析结果），
--     排查"这条会话到底跑在哪条层上"不再靠猜。
--
-- 幂等：MySQL 的 ADD COLUMN 不支持 IF NOT EXISTS，先查 information_schema 再执行。
-- ============================================================================

SET @c1 = (SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_llm_traces' AND COLUMN_NAME = 'channel');
SET @ddl = IF(@c1 = 0,
    'ALTER TABLE adh_llm_traces ADD COLUMN channel VARCHAR(32) NULL DEFAULT NULL COMMENT ''模型通道：qoder-gateway=Qoder网关（按credit），anthropic-direct=供应商直连（按token）。NULL 为历史数据，按 Qoder 口径解释''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @c2 = (SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_llm_traces' AND COLUMN_NAME = 'layer_id');
SET @ddl = IF(@c2 = 0,
    'ALTER TABLE adh_llm_traces ADD COLUMN layer_id BIGINT NULL DEFAULT NULL COMMENT ''实际生效的执行层 id（AS-BOT 绑定或默认层解析结果）''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @c3 = (SELECT COUNT(*) FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_llm_traces' AND COLUMN_NAME = 'layer_name');
SET @ddl = IF(@c3 = 0,
    'ALTER TABLE adh_llm_traces ADD COLUMN layer_name VARCHAR(100) NULL DEFAULT NULL COMMENT ''执行层业务名（展示用，不显示裸 id）''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 概览/用量要按通道聚合，缺索引会退化成全表扫
SET @i1 = (SELECT COUNT(*) FROM information_schema.STATISTICS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_llm_traces' AND INDEX_NAME = 'idx_trace_channel');
SET @ddl = IF(@i1 = 0,
    'ALTER TABLE adh_llm_traces ADD INDEX idx_trace_channel (channel, started_at)',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

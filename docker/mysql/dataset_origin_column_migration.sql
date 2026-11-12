-- ============================================================================
-- 数据集来源标注（origin）迁移 —— 幂等，可重复执行
--
-- 背景（看板图表取数收口为「只能挂数据集」）：
--   看板编辑器不再允许人写 SQL，取数口径一律来自数据集（BI 治理建模层）。收编
--   存量直连 SQL 图表时会批量新建数据集（见 backend/scripts/backfill_chart_datasets.py），
--   其中一部分正文来自 AI 会话产物（adh_charts.source_type='snapshot'）。
--   「这份口径是人写的、还是 AI 会话里生成后收编的」必须可查、可筛、可审计——
--   不能只把来源写进 description 自由文本里（那样既筛不出也容易被改掉）。
--   因此给它一个独立列，而不是硬塞进标签表（adh_tags 的 entity_type 枚举里没有 dataset）。
--
-- 取值（代码层常量见 dataset_service.DATASET_ORIGINS）：
--   manual          人工在数据集页/SQL Playground 创建（默认，= 存量行为）
--   chart_backfill  由看板直连 SQL 图表收编自动创建
--   ai_session      由 AI 会话产物（快照图）收编自动创建
--
-- 幂等: information_schema 判列后再 ALTER；重复执行只发一次 SELECT 1。
-- ============================================================================

USE adh;

-- 1) 加列（不存在才加，存量行按 DEFAULT 落 'manual'）
SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_datasets ADD COLUMN origin VARCHAR(32) NOT NULL DEFAULT ''manual'' COMMENT ''口径来源: manual|chart_backfill|ai_session''',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasets' AND COLUMN_NAME = 'origin');
PREPARE s1 FROM @ddl; EXECUTE s1; DEALLOCATE PREPARE s1;

-- 2) 索引（数据集页按来源筛选；列基数低，但清单页是常查路径）
SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_datasets ADD INDEX idx_origin (origin)',
    'SELECT 1') FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasets' AND INDEX_NAME = 'idx_origin');
PREPARE s2 FROM @ddl; EXECUTE s2; DEALLOCATE PREPARE s2;

-- 3) 存量行归位：只有 NULL/空值才置 manual（本迁移首跑前建的行 DEFAULT 已覆盖，
--    这里兜住"手工建表/半途执行"的历史库；已标注的收编行不得被冲回 manual）。
UPDATE adh_datasets SET origin = 'manual' WHERE origin IS NULL OR origin = '';

-- ----------------------------------------------------------------------------
-- 4) 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- SELECT origin, COUNT(*) FROM adh_datasets GROUP BY origin;
--   期望：收编前全部 manual；收编脚本跑完后新增行按 chart_backfill / ai_session 分布。
-- SHOW INDEX FROM adh_datasets WHERE Key_name = 'idx_origin';

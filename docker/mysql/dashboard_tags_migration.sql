-- ============================================================================
-- 看板标签列（幂等: DDL 经 information_schema 守卫，可重复执行）
--
-- 为什么要加：看板越来越多，管理页此前只有「名称/描述」过滤，同类看板难区分。
-- 增加**自由文本标签**（与 adh_metrics.tags 同口径：JSON 数组，一个看板可多标签），
-- 管理页据此生成「已用标签」芯片做过滤。标签非受控词表，纯人工分类，不进任何取数/权限判定。
--
-- 形状：与 layout/filters/params 一样由应用层 _json_loads_safe 解析；JSON 列经 pymysql
-- 回读为字符串，应用层补 '[]'（JSON 列不支持 DEFAULT），存量行默认空数组。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. tags 列（JSON 数组；已存在则跳过，重跑安全）
-- ----------------------------------------------------------------------------
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_dashboards'
      AND COLUMN_NAME = 'tags') = 0,
  'ALTER TABLE adh_dashboards ADD COLUMN tags JSON NULL COMMENT ''看板标签JSON数组(自由文本分类; 仅供管理页过滤, 不参与取数/权限)''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 2. 存量行补空数组（幂等：仅 tags IS NULL 时写入，重跑不再命中）
-- ----------------------------------------------------------------------------
UPDATE adh_dashboards SET tags = JSON_ARRAY() WHERE tags IS NULL;

-- ----------------------------------------------------------------------------
-- 3. 核验（人工执行后确认，勿删）───────────────────────────────
-- SHOW COLUMNS FROM adh_dashboards LIKE 'tags';
-- SELECT id, name, tags FROM adh_dashboards WHERE COALESCE(tags, '[]') <> '[]';
-- ----------------------------------------------------------------------------

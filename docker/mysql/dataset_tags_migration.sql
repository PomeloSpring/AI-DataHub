-- ============================================================================
-- 数据集标签列（tags）迁移 —— 幂等，可重复执行（不写 USE，库由连接决定）
--
-- 为什么要加：数据集已经有 100+ 份口径（域不同、物化目的不同，例如「售后域 /
-- 大屏物化 / 每日对账」），管理页只有名称/描述/数据源/可见范围四个维度，同类难挑。
-- 增加**自由文本标签**（与 adh_dashboards.tags 同口径：JSON 数组，一个数据集可多标签），
-- 管理页据此生成「已用标签」下拉做过滤。标签是纯人工分类，**不参与任何取数或权限裁决**
-- （可见性仍唯一由 dataset_visible_to / adh_role_dataset_access 决定）。
--
-- 形状：JSON 数组存字符串；pymysql 回读为字符串，应用层 normalize_tags 归一（JSON 列不支持 DEFAULT），
-- 存量行默认空数组。筛选走 JSON_CONTAINS 下推 SQL（list-pagination §2，不在前端切片）。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. tags 列（已存在则跳过，重跑安全）
-- ----------------------------------------------------------------------------
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasets'
      AND COLUMN_NAME = 'tags') = 0,
  'ALTER TABLE adh_datasets ADD COLUMN tags JSON NULL COMMENT ''数据集标签JSON数组(自由文本分类: 业务域/物化目的等, 仅管理页筛选, 不参与取数/权限)''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 2. 存量行补空数组（幂等：仅 tags IS NULL 时写入，重跑不再命中）
-- ----------------------------------------------------------------------------
UPDATE adh_datasets SET tags = JSON_ARRAY() WHERE tags IS NULL;

-- ----------------------------------------------------------------------------
-- 3. 核验（应用后人工确认，勿删）───────────────────────────────────────────
-- SHOW COLUMNS FROM adh_datasets LIKE 'tags';
-- SELECT id, name, tags FROM adh_datasets WHERE COALESCE(tags, '[]') <> '[]';
-- ----------------------------------------------------------------------------
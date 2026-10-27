-- ============================================================================
-- 指标口径标签独立成列（幂等: DDL 经 information_schema 守卫，数据回填可重复执行）
--
-- 为什么要拆：adh_metrics.category 的语义是**业务域**（可观测/客户/案例/订单…，
-- 由域标签派生与新建默认「业务」落库），而表单曾把「基础/衍生/复合」
-- （basic/derived/composite）也塞进同一列 —— 两个正交维度混写一列，
-- 结果分类下拉里业务域和口径标签混杂，选「基础指标」还会把域值写脏。
--
-- 收敛后：
--   category = 业务域分类（唯一语义，值域来自 dict_facets 聚合）
--   tags     = 口径标签 JSON 数组（基础/衍生/复合等，一行可多标签）
-- 后端 create/update 不再把 metric_type 写进 category；旧值一律归 tags。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. tags 列（JSON 数组，与 aliases 同形状；JSON 列不支持 DEFAULT，靠应用层补 '[]'）
-- ----------------------------------------------------------------------------
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_metrics'
      AND COLUMN_NAME = 'tags') = 0,
  'ALTER TABLE adh_metrics ADD COLUMN tags JSON NULL COMMENT ''口径标签JSON数组(基础/衍生/复合等; 与 category 业务域正交)''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 2. 存量脏值迁移：category 里写成口径表单值的行 → 中文标签落 tags，category 归空
--    （空 category 在字典页渲染为「未分类」，是事实——这些行从未有过业务域标注）
--    幂等：首次执行后 category 已归空，不再命中 WHERE，重跑不会重复追加标签。
--    不写嵌套引号的 CASE 表达式（'' 转义在 CASE 里跨客户端不可靠），一个旧值一条语句。
-- ----------------------------------------------------------------------------
UPDATE adh_metrics SET tags = JSON_ARRAY('基础'), category = NULL
 WHERE LOWER(TRIM(category)) = 'basic' OR TRIM(category) = '基础指标';
UPDATE adh_metrics SET tags = JSON_ARRAY('衍生'), category = NULL
 WHERE LOWER(TRIM(category)) = 'derived' OR TRIM(category) = '衍生指标';
UPDATE adh_metrics SET tags = JSON_ARRAY('复合'), category = NULL
 WHERE LOWER(TRIM(category)) = 'composite' OR TRIM(category) = '复合指标';

-- ----------------------------------------------------------------------------
-- 3. 核验（人工执行后确认，勿删）───────────────────────────────
-- SHOW COLUMNS FROM adh_metrics LIKE 'tags';
-- SELECT id, name, category, tags FROM adh_metrics WHERE COALESCE(tags, '[]') <> '[]';
-- SELECT DISTINCT category FROM adh_metrics;   -- 不应再出现 basic/derived/composite
-- ----------------------------------------------------------------------------

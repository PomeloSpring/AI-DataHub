-- ============================================================================
-- 表值图真分页：数据集级「支持分页」声明（adh_datasets.paginable）
-- ----------------------------------------------------------------------------
-- 背景（2026-11-11 用户拍板）：表值图此前是**伪分页** —— 后端一次把结果全拉到前端，
-- 前端再本地切片。`enableServerPagination` 开关在现网 13 张表值图里全是关的，
-- 于是「共 100 行」这句话挂在实际 446 万行的明细表上（截图那张 CASE 明细即此）。
-- 现在表值图恒定走服务端分页（page_size / page_num，pageSize 上限 100，档位 20/50/100）。
--
-- 为什么"是否分页"落在数据集而不是图表：能不能翻到第 N 页由**正文形状**决定
-- （明细行集 vs 统计汇总），而正文只在数据集层；图表只决定"要不要按页展示"。
-- 统计展示型数据集（GROUP BY / 聚合函数）本身就是完整的一页，给它套页限等于砍数据。
--
-- 幂等：DDL 经 information_schema 守卫；回填一律按正文形状重算，可重复执行。
-- ============================================================================

USE adh2;

-- ── 1) 加列（幂等）────────────────────────────────────────────────────────
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_datasets'
      AND COLUMN_NAME = 'paginable'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_datasets ADD COLUMN paginable TINYINT(1) NOT NULL DEFAULT 0
     COMMENT ''结果可按页取的明细行集：1=明细行集(表值图可翻页) 0=统计汇总(整页返回)'' AFTER chart_preset',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ── 2) 按正文形状回填 ─────────────────────────────────────────────────────
-- 判 1（明细行集）的三个条件，缺一即判 0：
--   · 有预制正文（sql_query 非空）—— 纯语义对象数据集走七闸门，产物没有 OFFSET/COUNT
--     形状，翻不了页，标 1 只会在刷新时报错；
--   · 正文不含 GROUP BY；
--   · 正文不含聚合函数（COUNT/SUM/AVG/MIN/MAX 后紧跟左括号）。
--     带括号是为了不把 `max_count`、`sum_x` 这类**列名**误判成聚合（列名后面不是括号）。
-- 判 0 时不覆盖人已显式改过的行以外的口径：这里是**全量重算**（不是只补 NULL），
-- 因为形状判定是正文的派生事实，正文改了它就该跟着变；人手动改过的由数据集页保存时
-- 显式提交 paginable 覆盖（update_dataset 只在请求带该字段时才写）。
UPDATE adh_datasets
SET paginable = CASE
    WHEN sql_query IS NOT NULL AND TRIM(sql_query) <> ''
         AND LOWER(sql_query) NOT LIKE '%group by%'
         AND LOWER(sql_query) NOT REGEXP '[^a-z0-9_](count|sum|avg|min|max)[[:space:]]*\\('
    THEN 1 ELSE 0
END;

-- ── 3) 核验（人工看结果，不自动改）────────────────────────────────────────
-- 期望：明细行集（CASE 明细 / 操作记录流水明细 / 操作画像明细 / 上传明细 / App 目录 /
-- Log 收集任务明细 / arms_rum_observability / 表值图）为 1，其余统计型为 0。
SELECT paginable,
       CASE WHEN es_pattern <> '' THEN 'es'
            WHEN sql_query IS NULL OR TRIM(sql_query) = '' THEN 'semantic'
            ELSE 'sql' END AS channel,
       COUNT(*) AS datasets
FROM adh_datasets
GROUP BY paginable, channel
ORDER BY paginable DESC, channel;

-- 表值图绑定的数据集是否都能翻页（paginable=0 的会显式提示"按行数上限展示"，不是缺陷）
SELECT c.id AS chart_id, c.name AS chart_name,
       d.id AS dataset_id, d.name AS dataset_name,
       CASE WHEN d.es_pattern <> '' THEN 'es'
            WHEN d.sql_query IS NULL OR TRIM(d.sql_query) = '' THEN 'semantic'
            ELSE 'sql' END AS channel,
       d.paginable
FROM adh_charts c
LEFT JOIN adh_datasets d ON d.id = c.source_id
WHERE c.chart_type = 'table_value'
ORDER BY d.paginable DESC, c.id;

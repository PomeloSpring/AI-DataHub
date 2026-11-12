-- ============================================================================
-- 看板遗留「查询参数」去重: 与 filters.pageParams 同名的 params 项剔除
--
-- 背景(2026-11-10 用户反馈"看板中的开始时间和结束时间是怎么来的，为什么不是时间
-- 选择器，看板编辑功能中也不呈现，无法操作"):
--   * 看板有两套参数命名空间 —— `adh_dashboards.params`(遗留"查询参数", 在页面顶部
--     渲染输入口) 与 `filters.pageParams`(看板参数, 由画布控件驱动);
--   * 现网 7 个看板把同一对 start_time/end_time **在三处各登记一次**: params、
--     pageParams、以及画布 widget_daterange 的 bind_param;
--   * 刷新下发是 `{...paramValues, ...pageParamValues}`, 同名键由后者覆盖 ——
--     顶部那两个文本框是"能填但不起作用"的死输入口(空承诺), 且 params 里存的
--     type 是 "string"(页面参数词汇), 渲染只认 type==='date' → 日期退化成文本框;
--   * 前端渲染带已收口为"画布控件驱动的键不再重复渲染"(DashboardParams.tsx),
--     本迁移把库里的重复定义一并清掉, 不留"代码是新口径、库是旧结构"的半截状态。
--
-- 口径:
--   * 只删 params 里**与 pageParams 同名**的项; 其余 params 原样保留(它们仍是
--     顶部参数带的合法输入口, 由渲染层按形状出日期选择器/选择框);
--   * pageParams 与控件 bind_param 不动 → 取数行为零变化(生效的一直是控件那份);
--   * params 为 NULL/[]/非法 JSON 的行不参与; 内容未变的行不写(updated_at 不抖)。
--
-- 幂等: 重复执行第二次起 0 行受影响。
--
-- 写法说明: `params`/`filters` 是 TEXT 列，MySQL 8 的 JSON_TABLE() 不接受表达式
-- （只接基础表列，CASE/CAST 包一层就报 1210 Incorrect arguments to JSON_TABLE），
-- 所以按下标展开数组（递归 CTE 出 0..63），并用 JSON_CONTAINS 判重名。
-- JSON_ARRAYAGG 会把 NULL 聚成 JSON null，所以剔除项用 WHERE 过滤 + LEFT JOIN 回补空数组。
-- MySQL 没有 JSON_EQUALS()，"内容未变就不写"用双向 JSON_CONTAINS 等价判定（不关心元素顺序）。
-- ============================================================================
USE adh;

UPDATE adh_dashboards d
JOIN (
  WITH RECURSIVE idx AS (SELECT 0 AS i UNION ALL SELECT i + 1 FROM idx WHERE i < 63),
  base AS (
    SELECT id, CAST(params AS JSON) AS params_doc, JSON_LENGTH(CAST(params AS JSON)) AS n,
           COALESCE(JSON_EXTRACT(filters, '$.pageParams'), JSON_ARRAY()) AS page
    FROM adh_dashboards
    WHERE params IS NOT NULL AND JSON_VALID(params)
      AND JSON_TYPE(CAST(params AS JSON)) = 'ARRAY'
      AND JSON_LENGTH(CAST(params AS JSON)) > 0
  ),
  picked AS (
    SELECT b.id AS id, JSON_EXTRACT(b.params_doc, CONCAT('$[', i.i, ']')) AS item,
           JSON_CONTAINS(b.page, JSON_OBJECT('name',
             JSON_UNQUOTE(JSON_EXTRACT(b.params_doc, CONCAT('$[', i.i, '].name'))))) AS dup
    FROM base b JOIN idx i ON i.i < b.n
  ),
  kept_agg AS (SELECT id, JSON_ARRAYAGG(item) AS kept FROM picked WHERE dup = 0 GROUP BY id)
  SELECT b.id, COALESCE(k.kept, JSON_ARRAY()) AS kept
  FROM base b LEFT JOIN kept_agg k ON k.id = b.id
) k ON k.id = d.id
SET d.params = CAST(k.kept AS CHAR)
WHERE NOT (JSON_CONTAINS(CAST(d.params AS JSON), k.kept)
           AND JSON_CONTAINS(k.kept, CAST(d.params AS JSON)));

-- 核验(手工执行): 期望所有行 params 与 pageParams 不再重名
-- SELECT id, name,
--        JSON_EXTRACT(filters, '$.pageParams[*].name') AS page_names,
--        JSON_EXTRACT(params, '$[*].name')             AS legacy_names
-- FROM adh_dashboards WHERE JSON_LENGTH(COALESCE(params, JSON_ARRAY())) > 0;

-- ============================================================================
-- 参数控件存量高度统一（2026-11-10）
--
-- 背景：参数控件的默认尺寸口径已切到「设备数据流」看板（见 frontend/src/lib/
-- dashboardDesign.ts 的 WIDGET_DEFAULT_WIDTH / WIDGET_DEFAULT_HEIGHT）：
--   ① 高度统一 10 行（40px），同一排控件上下对齐；
--   ② 宽度按控件类型分别配置（按钮 3 列、文本/下拉 5 列、日期选择器 9 列、日期区间 18 列）。
-- 旧默认按类型给 24/28/32/64px，落进页面网格就是 6/7/8 行混排，现网看板顶部控件参差。
--
-- 本迁移只把**已存过桌面布局的控件行高**抬到 10 行：
--   · 不改 x / w —— 改宽度会让同排控件横向重叠（例如日期区间 15→18 会压到右边的搜索按钮）；
--   · 抬高度经活库逐板核验无碰撞：所有含控件的看板，控件排最低边 ≤ 10，
--     其下第一张图表的最小 y ≥ 10（多数是 12/22），所以 10 行不会盖到下面的图。
-- 用户后来自己拖过的行高也会被统一到这里，这是本次口径变更的有意结果（控件尺寸不承载语义）。
--
-- 可重复执行：只改不等于 10 的行，跑第二次 0 行受影响。
-- ============================================================================

UPDATE adh_charts
SET config = JSON_SET(config, '$.pageLayout.desktop.h', 10)
WHERE LEFT(chart_type, 7) = 'widget_'
  AND config IS NOT NULL AND JSON_VALID(config)
  AND JSON_CONTAINS_PATH(config, 'one', '$.pageLayout.desktop.h')
  AND CAST(JSON_UNQUOTE(JSON_EXTRACT(config, '$.pageLayout.desktop.h')) AS SIGNED) <> 10;

-- tablet/mobile 若显式存过，同样收敛到同一行高（行单位在各断点都是 4px）
UPDATE adh_charts
SET config = JSON_SET(config, '$.pageLayout.tablet.h', 10)
WHERE LEFT(chart_type, 7) = 'widget_'
  AND config IS NOT NULL AND JSON_VALID(config)
  AND JSON_CONTAINS_PATH(config, 'one', '$.pageLayout.tablet.h')
  AND CAST(JSON_UNQUOTE(JSON_EXTRACT(config, '$.pageLayout.tablet.h')) AS SIGNED) <> 10;

UPDATE adh_charts
SET config = JSON_SET(config, '$.pageLayout.mobile.h', 10)
WHERE LEFT(chart_type, 7) = 'widget_'
  AND config IS NOT NULL AND JSON_VALID(config)
  AND JSON_CONTAINS_PATH(config, 'one', '$.pageLayout.mobile.h')
  AND CAST(JSON_UNQUOTE(JSON_EXTRACT(config, '$.pageLayout.mobile.h')) AS SIGNED) <> 10;

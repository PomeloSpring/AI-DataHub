-- 分析画布字模补录 — Chat 画布增强(数据画像卡片 / 关系图谱内嵌)所需字模
-- 幂等: INSERT IGNORE(uk_code 去重);内置块 source='system', is_builtin=1。
-- 消费方: frontend/src/components/DataProfileCard.tsx、GraphCard.tsx
--   (优先按 code 取字模样式,库中缺失时前端回落内置默认样式,不阻断渲染)。
-- 类目口径与 vis_library_migration.sql 一致: kpi_card / chart_style。

USE adh2;

-- 统计瓦片样式(kpi_card): 数据画像卡片的列统计块
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('card_profile_stat', '数据画像·统计瓦片', 'kpi_card', NULL,
   '{"config":{"cardBg":"hsl(var(--card))","cardBorder":"1px solid hsl(var(--border))","borderRadius":"10px","valueColor":"hsl(var(--foreground))","labelColor":"hsl(var(--muted-foreground))"}}',
   '数据画像卡片的列统计瓦片:缺失率/非空数等指标块,随主题取色', 'system', 1, 31);

-- 分布直方图样式(chart_style): 数据画像卡片的迷你分布图
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('cs_profile_hist', '数据画像·分布直方图', 'chart_style', 'bar',
   '{"config":{"colorScheme":["hsl(var(--chart-1))","hsl(var(--chart-2))"],"gradient":false,"borderRadius":2,"showLabel":false}}',
   '数据画像卡片的列分布迷你图(数值分箱/类别 TopN)', 'system', 1, 61),
  ('cs_graph_relation', '关系图谱·节点边样式', 'chart_style', 'graph',
   '{"config":{"colorScheme":["hsl(var(--chart-1))","hsl(var(--chart-2))","hsl(var(--chart-3))"],"nodeBg":"hsl(var(--card))","nodeBorder":"hsl(var(--border))","edgeColor":"hsl(var(--muted-foreground))","labelColor":"hsl(var(--foreground))"}}',
   '聊天画布关系图谱(节点/边/标签)的默认配色', 'system', 1, 62);

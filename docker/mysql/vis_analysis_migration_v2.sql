-- 分析画布字模补录(二) — 首页氛围背景 / 动画图表 / 数据网格样式
-- 幂等: INSERT IGNORE(uk_code 去重);内置块 source='system', is_builtin=1。
-- 消费方: Home(背景)、AnimatedTimeChart(动画图表)、DataGrid(网格)。
-- 可调参数(速率/topN/行高等)一律进 style_config,字模库改值即全局生效;
-- style_config 中缺失的键由前端内置默认补齐(useVisConfig 浅合并),故不对既有行做 UPDATE。

USE adh2;

-- 首页氛围背景(screen_background): 点阵纹理 + 双色光晕(全主题变量,随主题联动)
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('bg_home_ambient', '首页氛围·点阵光晕', 'screen_background', NULL,
   '{"backgroundColor":"hsl(var(--background))","backgroundImage":"radial-gradient(hsl(var(--muted-foreground) / 0.10) 1px, transparent 1px)","backgroundSize":"22px 22px","overlay":"radial-gradient(circle at 8% 12%, hsl(var(--primary) / 0.12), transparent 42%), radial-gradient(circle at 95% 35%, hsl(var(--accent) / 0.12), transparent 45%), radial-gradient(circle at 25% 105%, hsl(var(--primary) / 0.08), transparent 45%)"}',
   '聊天首页默认氛围: 点阵底纹 + 三处柔光,主题变量随主题联动', 'system', 1, 24);

-- 时间动画图表样式(chart_style): 竞速条形图 / 时序播放(速率、条数为可视化旋钮)
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('cs_bar_race', '竞速条形图·默认', 'chart_style', 'bar_race',
   '{"config":{"colorScheme":["hsl(var(--chart-1))","hsl(var(--chart-2))","hsl(var(--chart-3))"],"borderRadius":3,"topN":12,"speedMs":1400}}',
   '竞速条形图默认样式;topN=每帧条数,speedMs=每帧时长(播放速率旋钮)', 'system', 1, 63),
  ('cs_timeline_play', '时序播放·默认', 'chart_style', 'timeline_play',
   '{"config":{"colorScheme":["hsl(var(--chart-1))","hsl(var(--chart-2))"],"lineWidth":2,"speedMs":1400}}',
   '时序播放折线默认样式;speedMs=每帧时长', 'system', 1, 64);

-- 数据网格样式(chart_style): 查询结果/CSV 预览/Excel 编辑共用网格
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('cs_table_grid', '数据网格·默认', 'chart_style', 'table',
   '{"config":{"headerBg":"hsl(var(--muted) / 0.6)","zebraBg":"hsl(var(--muted) / 0.25)","rowHeight":30,"borderRadius":8}}',
   '统一数据网格默认样式;rowHeight 同时驱动虚拟滚动行高', 'system', 1, 65);

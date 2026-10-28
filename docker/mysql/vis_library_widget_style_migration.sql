-- ============================================================================
-- 字模库补全迁移：表值图样式（chart_style）+ 控件样式新分类（widget_style）
-- ----------------------------------------------------------------------------
-- 背景（两个都是「界面有开关、图上没反应」的空承诺）：
--   1) 内置 chart_style 只种了 bar/line/pie/radar/gauge/funnel 六类，表值图（table_value）没有字模；
--      而且表值图走 HTML 渲染，原先表头底色/表头文字色/行高/斑马纹/表格字号在
--      frontend/src/components/DashboardChart.tsx 里全是硬编码 —— 即使补数据也不会生效。
--      本批同时登记并消费 headerBg / headerColor / zebraBg / rowHeight / tableFontSize。
--   2) 参数控件（输入框/下拉/搜索按钮/日期范围/多选/按钮/标签）此前只能靠主题包的
--      widgets 段统一，没有独立可复用的字模；新增分类 widget_style（slot=widgetStyle），
--      旋钮走 WIDGET_KEYS 白名单，合并优先级：
--        主题包 widgets.default[<类型>] < 控件样式字模 < 图表卡显式 widgetStyle 覆盖。
--
-- 配色口径：与内置权威包 tp_as（AS 风格 / 设计服务 design-service 预设）同源，
--   主色 #0037CB、描边 #E9ECF8、正文 #262626、卡底 #ffffff；暗色一套沿用科技风 token。
--   （字模数据写具体色值是允许的；禁裸 hex 的口径只约束组件源码。）
--
-- 幂等：INSERT ... ON DUPLICATE KEY UPDATE（uk_vis_component_code 去重），可重复执行。
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1) 表值图样式（chart_style, chart_type=table_value）
-- ---------------------------------------------------------------------------
INSERT INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('cs_table_clean', '清爽表格', 'chart_style', 'table_value',
   '{"config":{"headerBg":"#F7F8FA","headerColor":"#262626","zebraBg":"#FAFBFC","rowHeight":32,"tableFontSize":13,"gridColor":"#E9ECF0"}}',
   '浅灰表头 + 细斑马纹 + 32px 行高，明细表默认档', 'system', 1, 68),
  ('cs_table_compact', '紧凑表格', 'chart_style', 'table_value',
   '{"config":{"headerBg":"#ffffff","headerColor":"#3A4149","zebraBg":"transparent","rowHeight":24,"tableFontSize":12,"gridColor":"#E9ECF0"}}',
   '无斑马纹 + 24px 行高 + 12px 字号，一屏看更多行的紧凑档', 'system', 1, 69)
ON DUPLICATE KEY UPDATE
  name = VALUES(name), chart_type = VALUES(chart_type), style_config = VALUES(style_config),
  description = VALUES(description), source = VALUES(source),
  is_builtin = VALUES(is_builtin), sort_order = VALUES(sort_order);

-- ---------------------------------------------------------------------------
-- 2) 控件样式（widget_style）：chart_type 为空 = 适用全部控件；逗号分隔可适用多类
--    键必须落在 style_config.widgetStyle 段（sanitizeStyle 按 WIDGET_KEYS 白名单裁剪）
--    ⚠ chart_type 取值必须是 frontend CHART_TYPES 里真实存在的类型（appliesToType 按它筛挂接对象）：
--      历史缺陷是本批曾登记 widget_input / widget_button 两个不存在的类型，
--      结果这两套字模在管理页看得见、在编辑器永远挂不上（compatible 永为 false）。
--      防线用例：frontend/src/lib/__tests__/dashboardDesign.test.ts「迁移里的类型名必须都真实存在」。
-- ---------------------------------------------------------------------------
INSERT INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('ws_control_as', '控件·AS 浅色', 'widget_style', NULL,
   '{"widgetStyle":{"backgroundColor":"#ffffff","textColor":"#262626","borderColor":"#E9ECF8","borderWidth":1,"borderRadius":8,"padding":"4px 10px","fontSize":13,"height":32,"gap":6,"labelWidth":72,"lineHeight":18}}',
   '通用控件基线（适用全部参数控件）：白底 + #E9ECF8 描边 + 8px 圆角 + 32px 高', 'system', 1, 70),
  ('ws_control_neon', '控件·科技风', 'widget_style', NULL,
   '{"widgetStyle":{"backgroundColor":"rgba(15,23,42,0.65)","textColor":"#e2e8f0","borderColor":"rgba(34,211,238,0.35)","borderWidth":1,"borderRadius":12,"padding":"6px 12px","fontSize":14,"height":36,"gap":8,"labelWidth":80,"boxShadow":"0 0 18px rgba(34,211,238,0.25)"}}',
   '大屏暗色控件：半透明深蓝底 + 霓虹描边 + 发光，与科技风主题包同口径', 'system', 1, 71),
  ('ws_select_as', '下拉框·AS', 'widget_style', 'widget_select',
   '{"widgetStyle":{"backgroundColor":"#ffffff","textColor":"#262626","borderColor":"#E9ECF8","borderWidth":1,"borderRadius":8,"padding":"4px 10px","fontSize":13,"height":32,"labelWidth":84}}',
   '单选下拉：与输入框同描边同高度，避免出现两种控件高度', 'system', 1, 72),
  ('ws_input_as', '输入框·AS', 'widget_style', 'widget_text,widget_number',
   '{"widgetStyle":{"backgroundColor":"#ffffff","textColor":"#262626","borderColor":"#E9ECF8","borderWidth":1,"borderRadius":8,"padding":"4px 10px","fontSize":13,"height":32,"labelWidth":84}}',
   '文本/数字输入框：浅描边 + 8px 圆角，聚焦态由浏览器原生 outline 承担', 'system', 1, 73),
  ('ws_search_as', '查询按钮·主色', 'widget_style', 'widget_search',
   '{"widgetStyle":{"backgroundColor":"#0037CB","textColor":"#ffffff","borderColor":"#0037CB","borderWidth":1,"borderRadius":8,"padding":"5px 16px","fontSize":13,"fontWeight":500,"height":32,"gap":6}}',
   '查询按钮：主色实心白字，与图表主色一致（禁用态由组件降透明度）', 'system', 1, 74),
  ('ws_button_as', '按钮·主色', 'widget_style', 'widget_reset,widget_export',
   '{"widgetStyle":{"backgroundColor":"#0037CB","textColor":"#ffffff","borderColor":"#0037CB","borderWidth":1,"borderRadius":8,"padding":"5px 16px","fontSize":13,"fontWeight":500,"height":32,"gap":6}}',
   '重置/导出按钮：与查询按钮同一套主色口径，避免两处按钮长得不一样（查询按钮另有专档 ws_search_as）', 'system', 1, 75),
  ('ws_daterange_as', '日期区间·AS', 'widget_style', 'widget_daterange',
   '{"widgetStyle":{"backgroundColor":"#ffffff","textColor":"#262626","borderColor":"#E9ECF8","borderWidth":1,"borderRadius":8,"padding":"4px 10px","fontSize":13,"height":32,"gap":6,"labelWidth":72}}',
   '日期区间：内层两个日期框与快捷下拉共用同一描边/圆角/间距', 'system', 1, 76),
  ('ws_multiselect_as', '多选框·AS', 'widget_style', 'widget_multi_select',
   '{"widgetStyle":{"backgroundColor":"#ffffff","textColor":"#262626","borderColor":"#E9ECF8","borderWidth":1,"borderRadius":8,"padding":"4px 8px","fontSize":13,"height":64,"gap":6,"labelWidth":72}}',
   '多选框：高度交给「控件高」旋钮（此处给 64 的默认档），不再写死 60', 'system', 1, 77),
  ('ws_label_as', '文本标签·无边框', 'widget_style', 'widget_label',
   '{"widgetStyle":{"backgroundColor":"transparent","textColor":"#3A4149","borderWidth":0,"borderRadius":0,"padding":"2px 0","fontSize":13,"lineHeight":18,"labelWidth":0}}',
   '纯文本标签：无底无边，只统一字号与行高（labelWidth=0 让标签不占额外列宽）', 'system', 1, 78)
ON DUPLICATE KEY UPDATE
  name = VALUES(name), chart_type = VALUES(chart_type), style_config = VALUES(style_config),
  description = VALUES(description), source = VALUES(source),
  is_builtin = VALUES(is_builtin), sort_order = VALUES(sort_order);

-- ---------------------------------------------------------------------------
-- 4) 核验（人工执行，不参与建库）
--    SELECT code, name, category, chart_type, is_builtin FROM adh_vis_components
--      WHERE category = 'widget_style' OR chart_type = 'table_value' ORDER BY sort_order;
--    悬空类型自检（应返回 0 行）：
--    SELECT code, chart_type FROM adh_vis_components WHERE category = 'widget_style'
--      AND chart_type IS NOT NULL AND chart_type NOT REGEXP 'widget_(label|text|number|date|daterange|select|multi_select|search|reset|export)$';
--    预期：11 行（2 个 table_value 图表样式 + 9 个控件样式），is_builtin 全为 1。
--    SELECT JSON_VALID(style_config) FROM adh_vis_components WHERE code LIKE 'ws_%';
--    预期：全部为 1（JSON 损坏会被 sanitizeStyle 判空，表现为"选了没反应"）。
-- ---------------------------------------------------------------------------

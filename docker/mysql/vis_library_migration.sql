-- ============================================================================
-- 可视化字模库(UI 库 / "活字")迁移脚本
-- ----------------------------------------------------------------------------
-- 把大屏可复用元素做成一条条"字模"存库: 系统内置(admin-only 保护)+ 新增回存,
-- 全局共享(workspace_id=0), 供 AS-BOT 与前端组装可视化大屏时取用。
-- category: chart_style / screen_background / kpi_card / layout_template /
--           decoration_frame / color_theme / sql_template
-- 幂等: 建表 IF NOT EXISTS; 内置种子用 INSERT IGNORE(uk_code 去重)。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_vis_components (
  id             BIGINT       NOT NULL AUTO_INCREMENT,
  code           VARCHAR(64)  NOT NULL COMMENT '全局唯一字模编码',
  name           VARCHAR(128) NOT NULL COMMENT '展示名',
  category       VARCHAR(32)  NOT NULL COMMENT '字模类别',
  chart_type     VARCHAR(32)  NULL     COMMENT '图表样式字模适用的图表类型(可空)',
  style_config   TEXT         NULL     COMMENT '样式配置 JSON(前端按 category 解释)',
  thumbnail      TEXT         NULL     COMMENT '预览缩略图(data-uri / url / 内联 svg)',
  query_template TEXT         NULL     COMMENT 'SQL 模板字模的参数化查询 JSON(仅 sql_template)',
  description    VARCHAR(512) DEFAULT '',
  source         VARCHAR(16)  DEFAULT 'custom' COMMENT 'system=内置 | custom=新增',
  workspace_id   BIGINT       DEFAULT 0 COMMENT '0=全局共享',
  is_active      TINYINT      DEFAULT 1,
  is_builtin     TINYINT      DEFAULT 0 COMMENT '1=内置, admin-only 编辑/删除',
  sort_order     INT          DEFAULT 0,
  created_by     BIGINT       DEFAULT 0,
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_vis_component_code (code),
  INDEX idx_vis_category (category, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================================
-- 内置字模种子(source=system, is_builtin=1)
-- ============================================================================

-- 配色主题包(color_theme): 复用全站主题风格作为大屏配色字模
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('theme_tech_neon', '科技霓虹', 'color_theme', NULL,
   '{"mode":"dark","palette":["#22d3ee","#3b82f6","#8b5cf6","#22d3ee","#a78bfa","#38bdf8"],"viewBg":"#0b1220","popoverBg":"#0f172a"}',
   '深蓝底 + 霓虹高亮, 适合实时监控类大屏', 'system', 1, 10),
  ('theme_finance_gold', '金融金', 'color_theme', NULL,
   '{"mode":"dark","palette":["#d4af37","#e5c76b","#b8912f","#f0d888","#c9a227","#8a6d1f"],"viewBg":"#0a1628","popoverBg":"#0f2138"}',
   '深海军蓝 + 金色主调, 适合经营驾驶舱', 'system', 1, 11),
  ('theme_datafoundry', 'DataFoundry 近黑', 'color_theme', NULL,
   '{"mode":"light","palette":["#111827","#1f2937","#374151","#4b5563","#6b7280","#9ca3af"],"viewBg":"#ffffff","popoverBg":"#f9fafb"}',
   '克制优雅, 近黑主色 + 宝石色调', 'system', 1, 12),
  ('theme_glass', '玻璃拟态', 'color_theme', NULL,
   '{"mode":"dark","palette":["#60a5fa","#a78bfa","#f472b6","#34d399","#fbbf24","#22d3ee"],"viewBg":"#111827","popoverBg":"#1f2937"}',
   '深色半透明毛玻璃质感', 'system', 1, 13),
  ('theme_aurora', '极光渐变', 'color_theme', NULL,
   '{"mode":"dark","palette":["#34d399","#22d3ee","#818cf8","#c084fc","#f472b6","#fb7185"],"viewBg":"#0f172a","popoverBg":"#111827"}',
   '多彩渐变, 适合增长/运营分析大屏', 'system', 1, 14);

-- 大屏背景(screen_background)
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('bg_tech_grid', '科技网格·深蓝', 'screen_background', NULL,
   '{"backgroundColor":"#0a1220","backgroundImage":"linear-gradient(rgba(34,211,238,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(34,211,238,0.06) 1px, transparent 1px)","backgroundSize":"40px 40px","overlay":"radial-gradient(circle at 50% 0%, rgba(59,130,246,0.15), transparent 60%)"}',
   '深蓝网格底纹, 数据大屏经典背景', 'system', 1, 20),
  ('bg_dark_aurora', '暗夜极光', 'screen_background', NULL,
   '{"backgroundColor":"#0b1020","backgroundImage":"radial-gradient(circle at 15% 20%, rgba(129,140,248,0.20), transparent 40%), radial-gradient(circle at 85% 80%, rgba(34,211,238,0.18), transparent 40%)"}',
   '深色 + 双色光晕, 科技感强', 'system', 1, 21),
  ('bg_finance_navy', '金融深蓝', 'screen_background', NULL,
   '{"backgroundColor":"#081226","backgroundImage":"linear-gradient(180deg,#0b1a33 0%,#081226 100%)"}',
   '沉稳深蓝渐变, 适合经营/财务大屏', 'system', 1, 22),
  ('bg_light_clean', '简洁亮白', 'screen_background', NULL,
   '{"backgroundColor":"#f8fafc","backgroundImage":"none"}',
   '浅色干净, 适合报表型看板', 'system', 1, 23);

-- 卡片/容器样式(kpi_card): 指标卡风格
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('card_glow', '霓虹发光卡', 'kpi_card', NULL,
   '{"config":{"cardBg":"rgba(15,23,42,0.65)","cardBorder":"1px solid rgba(34,211,238,0.35)","borderRadius":"12px","glow":"0 0 18px rgba(34,211,238,0.25)","valueColor":"#22d3ee","labelColor":"#94a3b8"}}',
   '半透明 + 霓虹描边发光, 配合科技背景', 'system', 1, 30),
  ('card_flat', '极简扁平卡', 'kpi_card', NULL,
   '{"config":{"cardBg":"#ffffff","cardBorder":"1px solid #e5e7eb","borderRadius":"8px","glow":"none","valueColor":"#111827","labelColor":"#6b7280"}}',
   '浅色扁平, 近黑主色', 'system', 1, 31),
  ('card_gold', '金色高端卡', 'kpi_card', NULL,
   '{"config":{"cardBg":"rgba(10,22,40,0.7)","cardBorder":"1px solid rgba(212,175,55,0.4)","borderRadius":"10px","glow":"0 0 16px rgba(212,175,55,0.18)","valueColor":"#e5c76b","labelColor":"#94a3b8"}}',
   '深蓝底金边, 金融驾驶舱质感', 'system', 1, 32);

-- 装饰边框 / 标题栏(decoration_frame)
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('frame_tech_corner', '科技角标框', 'decoration_frame', NULL,
   '{"config":{"cornerAccent":"#22d3ee","showCornerBrackets":true,"borderStyle":"1px solid rgba(148,163,184,0.25)"}}',
   '四角高亮括标, 大屏常见科技边框', 'system', 1, 40),
  ('frame_title_bar', '渐变标题栏', 'decoration_frame', NULL,
   '{"config":{"titleBarBg":"linear-gradient(90deg, rgba(34,211,238,0.18), transparent)","titleColor":"#e2e8f0","showUnderline":true,"underlineColor":"#22d3ee"}}',
   '顶部渐变标题栏 + 下划线', 'system', 1, 41);

-- 布局模板(layout_template)
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('layout_kpi_top', '顶部指标 + 多栏', 'layout_template', NULL,
   '{"grid":{"canvasW":1920,"canvasH":1080,"kpiRow":{"height":180,"count":4},"rows":[{"cols":3,"ratio":1},{"cols":2,"ratio":1}]}}',
   '顶部一排 KPI + 下方 3/2 栏, 经典驾驶舱布局', 'system', 1, 50),
  ('layout_left_right', '左主右辅', 'layout_template', NULL,
   '{"grid":{"canvasW":1920,"canvasH":1080,"columns":[{"width":1280,"role":"primary"},{"width":640,"role":"secondary"}]}}',
   '左侧主图 + 右侧辅助列, 适合焦点分析', 'system', 1, 51),
  ('layout_grid_3x3', '九宫格', 'layout_template', NULL,
   '{"grid":{"canvasW":1920,"canvasH":1080,"rows":3,"cols":3}}',
   '均分 3x3 网格, 适合指标密集型监控', 'system', 1, 52);

-- 图表样式字模(chart_style): 不同视觉风格 × 图表类型
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('cs_bar_neon', '霓虹渐变柱', 'chart_style', 'bar',
   '{"config":{"colorScheme":["#22d3ee","#3b82f6"],"gradient":true,"borderRadius":6,"showLabel":false,"axisStyle":{"gridColor":"rgba(148,163,184,0.15)"}}}',
   '青蓝渐变圆角柱图, 大屏常用', 'system', 1, 60),
  ('cs_bar_flat', '近黑扁平柱', 'chart_style', 'bar',
   '{"config":{"colorScheme":["#111827"],"gradient":false,"borderRadius":2,"showLabel":true}}',
   '单系列近黑柱图(对齐 DataFoundry 规范)', 'system', 1, 61),
  ('cs_line_tech', '荧光折线', 'chart_style', 'line',
   '{"config":{"colorScheme":["#22d3ee"],"lineWidth":2.5,"smooth":true,"areaFill":true,"areaOpacity":0.15,"glow":true}}',
   '平滑荧光折线 + 轻面积填充', 'system', 1, 62),
  ('cs_line_gold', '金色趋势线', 'chart_style', 'line',
   '{"config":{"colorScheme":["#d4af37"],"lineWidth":2,"smooth":true,"areaFill":false}}',
   '金融风金色趋势线', 'system', 1, 63),
  ('cs_pie_ring', '环形占比', 'chart_style', 'pie',
   '{"config":{"innerRadius":0.55,"colorScheme":["#22d3ee","#3b82f6","#8b5cf6","#f472b6","#fbbf24"],"showLabel":true,"legend":"right"}}',
   '低饱和多色环形图', 'system', 1, 64),
  ('cs_radar_tech', '科技雷达', 'chart_style', 'radar',
   '{"config":{"colorScheme":["#22d3ee"],"areaOpacity":0.25,"gridColor":"rgba(34,211,238,0.25)"}}',
   '多维对比雷达图, 霓虹描边', 'system', 1, 65),
  ('cs_gauge_ring', '环形仪表', 'chart_style', 'gauge',
   '{"config":{"colorScheme":["#22d3ee","#8b5cf6"],"thresholds":[0.6,0.85],"valueColor":"#e2e8f0"}}',
   '进度/达成率仪表卡', 'system', 1, 66),
  ('cs_funnel_grad', '渐变漏斗', 'chart_style', 'funnel',
   '{"config":{"colorScheme":["#22d3ee","#3b82f6","#8b5cf6","#c084fc"],"gradient":true}}',
   '转化漏斗, 渐变分层', 'system', 1, 67);

-- SQL 模板字模(sql_template): 供传统 BI 管理员/数据开发手动配置的参数化模板
INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, query_template, description, source, is_builtin, sort_order)
VALUES
  ('sql_trend_daily', '日度趋势(参数化)', 'sql_template', 'line',
   '{"params":[{"name":"start_date","type":"date"},{"name":"end_date","type":"date"}]}',
   '{"sql":"SELECT stat_date AS x, SUM(amount) AS y FROM {{table}} WHERE stat_date BETWEEN {{start_date}} AND {{end_date}} GROUP BY stat_date ORDER BY stat_date","bindTable":true}',
   '按日期区间聚合金额的趋势模板, 人工在图表配置里选表填参', 'system', 1, 70),
  ('sql_top_n', '维度 TopN(参数化)', 'sql_template', 'bar',
   '{"params":[{"name":"n","type":"number","default":10}]}',
   '{"sql":"SELECT {{dim}} AS x, SUM(amount) AS y FROM {{table}} GROUP BY {{dim}} ORDER BY y DESC LIMIT {{n}}","bindTable":true}',
   '取某维度金额 TopN 的柱图模板', 'system', 1, 71);

-- ============================================================================
-- AS 风格主题包（字模库 theme_pack）迁移脚本
-- ----------------------------------------------------------------------------
-- 素材来源：公司内部「设计服务图表主题包 design-chart-theme-kit」
--   · 预设 design-service（预览页 index.html 的默认态，用户指定为权威视觉）
--   · 壳层 token 取 demo/demo.css（page-bg/card-bg/border/text/chip/accent）
--   · 图表 token 取 src/tokens.js（bar 渐隐、bar.cap 柱顶帽、line/area、pie 8 色板）
--   · 字模取 src/typography.js（轴标签/图例 12px，折线 2px，tooltip 白卡 2px 圆角）
--   注：素材 README 声明「暗色无独立设计稿、为近似值」，本包 dark 段与上游同源，未另行发挥。
--
-- 落地口径（.qoder/rules/ui-vis-library-style.md）：
--   1) 系统明暗主题的就地换皮在 frontend/src/styles/globals.css（:root=暗, .light=亮），
--      本脚本同步把 tp_dark / tp_light 两套「跟随主题」的包换成 AS 口径 —— 否则会出现
--      「页面外壳是 AS、图表仍是中性灰」的半截状态（= 样式缺陷）。
--   2) 新增样式键 barFade / barCap / axisLabelSize 已进 dashboardDesign.VISUAL_KEYS 白名单，
--      并由 DashboardChart 的 bar/timeseries_bar/默认柱图分支与 axisLabelStyle 真实消费。
--   3) themePackFor(theme) 用 `tp_<theme>` 反查，故「跟随明暗态」的两个包必须叫 tp_light/tp_dark；
--      tp_as 是用户点名的「AS风格」包，供看板/大屏一键套用。三者同源，改一处必须同步另两处。
--
-- 幂等：INSERT ... ON DUPLICATE KEY UPDATE（uk_vis_component_code 去重），可重复执行。
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1) AS风格（浅色，权威设计态）：一键套用整站风格
-- ---------------------------------------------------------------------------
INSERT INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('tp_as', 'AS 风格', 'theme_pack', NULL,
   '{"mode":"light","viewBg":"#ffffff","popoverBg":"#ffffff","textColor":"#262626","subTextColor":"#3A4149","gridColor":"#E9ECF0","palette":["#0037CB","#38AD2A","#E56F22","#08997E","#7C4DFF","#D81B60","#00A3C4","#8A939F"],"background":{"backgroundColor":"#F7F8FA","backgroundImage":"none"},"card":{"cardBg":"#ffffff","cardBorder":"1px solid #E9ECF8","cardRadius":12,"glow":"0 4px 8px 0 rgba(11,106,229,0.08)","labelColor":"#3A4149","valueColor":"#262626"},"decoration":{"titleColor":"#262626","titleFontSize":15,"titleFontWeight":600},"charts":{"default":{"barFade":true,"barCap":"#0037CB","gradient":false,"borderRadius":0,"smooth":false,"lineWidth":2,"showLabel":false,"axisLabelSize":12,"gridColor":"#E9ECF0"},"bar":{"barFade":true,"barCap":"#0037CB","borderRadius":0},"timeseries_bar":{"barFade":true,"barCap":"#0037CB","borderRadius":0},"line":{"smooth":false,"lineWidth":2,"areaFill":true,"areaOpacity":0.5},"timeseries_line":{"smooth":false,"lineWidth":2},"area":{"smooth":false,"areaOpacity":0.5},"timeseries_area":{"areaOpacity":0.5},"pie":{"innerRadius":0.58,"showLabel":true,"legend":"bottom"},"radar":{"areaOpacity":0.25,"lineWidth":2},"gauge":{"thresholds":[0.6,0.85],"valueColor":"#262626"}},"widgets":{"default":{"backgroundColor":"#ffffff","textColor":"#262626","borderColor":"#E9ECF8","borderWidth":1,"borderRadius":8,"padding":8,"fontSize":13}}}',
   '公司设计服务图表主题包(design-service 预设·浅色)：#0037CB 主色 + 柱体上深下浅渐隐 + 4px 柱顶帽 + 环形饼图底部图例；与 tp_light/tp_dark 同源',
   'system', 1, 4)
ON DUPLICATE KEY UPDATE
  name = VALUES(name), style_config = VALUES(style_config),
  description = VALUES(description), source = VALUES(source),
  is_builtin = VALUES(is_builtin), sort_order = VALUES(sort_order);

-- ---------------------------------------------------------------------------
-- 2) tp_light：亮色主题的默认包 → 换成 AS 浅色（与 tp_as 同口径）
-- ---------------------------------------------------------------------------
UPDATE adh_vis_components
SET name = 'AS 风格（跟随亮色主题）',
    description = '亮色主题反查包（themePackFor("light")）；取值为 AS 风格 design-service 浅色预设，与 tp_as 同源',
    style_config = '{"mode":"light","viewBg":"#ffffff","popoverBg":"#ffffff","textColor":"#262626","subTextColor":"#3A4149","gridColor":"#E9ECF0","palette":["#0037CB","#38AD2A","#E56F22","#08997E","#7C4DFF","#D81B60","#00A3C4","#8A939F"],"background":{"backgroundColor":"#F7F8FA","backgroundImage":"none"},"card":{"cardBg":"#ffffff","cardBorder":"1px solid #E9ECF8","cardRadius":12,"glow":"0 4px 8px 0 rgba(11,106,229,0.08)","labelColor":"#3A4149","valueColor":"#262626"},"decoration":{"titleColor":"#262626","titleFontSize":15,"titleFontWeight":600},"charts":{"default":{"barFade":true,"barCap":"#0037CB","gradient":false,"borderRadius":0,"smooth":false,"lineWidth":2,"showLabel":false,"axisLabelSize":12,"gridColor":"#E9ECF0"},"bar":{"barFade":true,"barCap":"#0037CB","borderRadius":0},"timeseries_bar":{"barFade":true,"barCap":"#0037CB","borderRadius":0},"line":{"smooth":false,"lineWidth":2,"areaFill":true,"areaOpacity":0.5},"timeseries_line":{"smooth":false,"lineWidth":2},"area":{"smooth":false,"areaOpacity":0.5},"timeseries_area":{"areaOpacity":0.5},"pie":{"innerRadius":0.58,"showLabel":true,"legend":"bottom"},"radar":{"areaOpacity":0.25,"lineWidth":2},"gauge":{"thresholds":[0.6,0.85],"valueColor":"#262626"}},"widgets":{"default":{"backgroundColor":"#ffffff","textColor":"#262626","borderColor":"#E9ECF8","borderWidth":1,"borderRadius":8,"padding":8,"fontSize":13}}}'
WHERE code = 'tp_light';

-- ---------------------------------------------------------------------------
-- 3) tp_dark：暗色主题的默认包 → 换成 AS 暗色（素材 dark 段，近似值口径不变）
-- ---------------------------------------------------------------------------
UPDATE adh_vis_components
SET name = 'AS 风格（跟随暗色主题）',
    description = '暗色主题反查包（themePackFor("dark")）；取值为 AS 风格 design-service 暗色段（素材标注无独立设计稿、为近似值）',
    style_config = '{"mode":"dark","viewBg":"#1D2229","popoverBg":"#1D2229","textColor":"#E7E8E8","subTextColor":"#ACADB0","gridColor":"#475566","palette":["#809BE5","#5FBF4A","#E57D39","#65C2B1","#A48BFF","#EC5B90","#4FC3DE","#A0A2A5"],"background":{"backgroundColor":"#171B21","backgroundImage":"none"},"card":{"cardBg":"#1D2229","cardBorder":"1px solid #2C3E57","cardRadius":12,"glow":"0 4px 8px 0 rgba(0,0,0,0.4)","labelColor":"#ACADB0","valueColor":"#E7E8E8"},"decoration":{"titleColor":"#E7E8E8","titleFontSize":15,"titleFontWeight":600},"charts":{"default":{"barFade":true,"barCap":"#809BE5","gradient":false,"borderRadius":0,"smooth":false,"lineWidth":2,"showLabel":false,"axisLabelSize":12,"gridColor":"#475566"},"bar":{"barFade":true,"barCap":"#809BE5","borderRadius":0},"timeseries_bar":{"barFade":true,"barCap":"#809BE5","borderRadius":0},"line":{"smooth":false,"lineWidth":2,"areaFill":true,"areaOpacity":0.35},"timeseries_line":{"smooth":false,"lineWidth":2},"area":{"smooth":false,"areaOpacity":0.35},"timeseries_area":{"areaOpacity":0.35},"pie":{"innerRadius":0.58,"showLabel":true,"legend":"bottom"},"radar":{"areaOpacity":0.25,"lineWidth":2},"gauge":{"thresholds":[0.6,0.85],"valueColor":"#E7E8E8"}},"widgets":{"default":{"backgroundColor":"#1D2229","textColor":"#E7E8E8","borderColor":"#2C3E57","borderWidth":1,"borderRadius":8,"padding":8,"fontSize":13}}}'
WHERE code = 'tp_dark';

-- ---------------------------------------------------------------------------
-- 4) 核验（人工执行，不参与建库）
--    SELECT code, name, category, is_builtin, LEFT(style_config, 80)
--      FROM adh_vis_components WHERE code IN ('tp_as','tp_light','tp_dark');
--    预期：三行均存在，name 全部含「AS 风格」，style_config 含 barFade/barCap/axisLabelSize。
-- ---------------------------------------------------------------------------

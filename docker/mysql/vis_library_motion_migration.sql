-- ============================================================================
-- 界面动效预设（motion_preset）种子迁移
-- ----------------------------------------------------------------------------
-- 定位：把「出现效果」（入场 / 图表入场 / 切板转场 / 正文出现）做成可配置字模。
--
-- 为什么与主题包（tp_*）正交、单独一个分类：
--   主题管配色与版式，动效管时间维度 —— 每套主题都能搭配任意动效预设。
--   把动效塞进 tp_* 会出现「换配色被迫换动画」「同一动效要在 9 套包里各抄一份」，
--   所以这里不写 charts/widgets 那种嵌套段，也不参与 sanitizePack。
--   解析唯一入口 = frontend/src/lib/motion.ts（resolveMotion），CSS 只读变量。
--
-- style_config 四段（键清单与 MOTION_SECTION_KEYS 一一对应，漏登记=入库即剥）：
--   enter.effect      fadeUp | fade | slideDown | slideLeft | scale | blur | wipeDown | none
--   enter.durationMs / easing(standard|snappy|linear) / staggerMs / maxStaggerMs / offsetPx
--   chart.appear      default(=不往 G2 spec 写 animate，与改造前逐字节相同) | none |
--                     fadeIn | pathIn | growInY | waveIn | zoomIn（值为 @antv/g2 注册表名）
--   transition.effect none | fadeIn | pushLeft | zoomIn | wipeDown
--   reveal.effect     none | fadeIn | sectionStagger（后者同 fadeIn，只是按分段错峰延迟）
--
-- 五档非默认预设各占一条**不同**的入场通道（blur / scale / wipeDown / pop + none），
-- 不再都是 fadeUp 的时长变体：只换时长与 10~20px 位移，用户肉眼分不出差别（已发生过的反馈）。
-- 参数被拉开过一轮，已应用旧 seed 的库由 vis_library_motion_v2_migration.sql 就地校准。
-- mp_default 逐段等于现网实现（改它=改默认观感，需单独拍板）：
--   enter = 0.45s + cubic-bezier(0.22,1,0.36,1) + 10px 位移 + 90ms 错峰（cap 450ms）
--           = globals.css 里 stageSlideIn 那一条；
--   chart = default、transition/reveal = none（今天切板是硬换、正文一次性出现）。
--
-- 幂等：INSERT IGNORE（uk_vis_component_code 去重），可重复执行；
--       内置行 is_builtin=1（仅 admin 可编辑/下架，对齐内置组件保护规范）。
-- ============================================================================

INSERT IGNORE INTO adh_vis_components
  (code, name, category, chart_type, style_config, description, source, is_builtin, sort_order)
VALUES
  ('mp_default', '默认（现网）', 'motion_preset', NULL,
   '{"enter":{"effect":"fadeUp","durationMs":450,"easing":"standard","staggerMs":90,"maxStaggerMs":450,"offsetPx":10},"chart":{"appear":"default","durationMs":600,"easing":"standard"},"transition":{"effect":"none","durationMs":500,"easing":"standard"},"reveal":{"effect":"none","staggerMs":90}}',
   '不改变现状的基准档：卡片自下而上滑入 0.45s、图表动画跟随 G2 默认、切板硬换、正文一次性出现', 'system', 1, 80),
  ('mp_none', '关闭动效', 'motion_preset', NULL,
   '{"enter":{"effect":"none","durationMs":0,"easing":"standard","staggerMs":0,"maxStaggerMs":0,"offsetPx":0},"chart":{"appear":"none","durationMs":0,"easing":"linear"},"transition":{"effect":"none","durationMs":0,"easing":"linear"},"reveal":{"effect":"none","staggerMs":0}}',
   '全部内容直接落位：入场/转场/正文均不加动画，图表 G2 动画一并关掉（animate=false，给长期挂机与低配终端）', 'system', 1, 81),
  ('mp_soft', '柔和舒缓', 'motion_preset', NULL,
   '{"enter":{"effect":"blur","durationMs":700,"easing":"standard","staggerMs":140,"maxStaggerMs":700,"offsetPx":16},"chart":{"appear":"fadeIn","durationMs":700,"easing":"standard"},"transition":{"effect":"fadeIn","durationMs":700,"easing":"standard"},"reveal":{"effect":"fadeIn","staggerMs":140}}',
   '卡片由模糊渐显（不靠位移，700ms 慢一档）、图表与切板整体淡入：汇报讲解向，观感沉稳不抢戏', 'system', 1, 82),
  ('mp_crisp', '利落短促', 'motion_preset', NULL,
   '{"enter":{"effect":"scale","durationMs":180,"easing":"snappy","staggerMs":0,"maxStaggerMs":0,"offsetPx":0},"chart":{"appear":"none","durationMs":0,"easing":"linear"},"transition":{"effect":"fadeIn","durationMs":200,"easing":"snappy"},"reveal":{"effect":"none","staggerMs":0}}',
   '卡片快速放大落位 0.18s、无错峰无位移、图表不做动画：数据密集型工作台，逐次交互不等人', 'system', 1, 83),
  ('mp_tech_flow', '科技流光', 'motion_preset', NULL,
   '{"enter":{"effect":"wipeDown","durationMs":520,"easing":"standard","staggerMs":110,"maxStaggerMs":640,"offsetPx":12},"chart":{"appear":"pathIn","durationMs":900,"easing":"standard"},"transition":{"effect":"wipeDown","durationMs":600,"easing":"standard"},"reveal":{"effect":"fadeIn","staggerMs":110}}',
   '擦除展开 + 折线描画：深色实时监控大屏向，切板与图表入场同一条科技感节奏', 'system', 1, 84),
  ('mp_presentation', '演示播报', 'motion_preset', NULL,
   '{"enter":{"effect":"pop","durationMs":460,"easing":"standard","staggerMs":180,"maxStaggerMs":1100,"offsetPx":28},"chart":{"appear":"growInY","durationMs":700,"easing":"standard"},"transition":{"effect":"pushLeft","durationMs":550,"easing":"standard"},"reveal":{"effect":"sectionStagger","staggerMs":160}}',
   '卡片逐块弹入落位（带一拍回弹，错峰 180ms 到 1.1s）、柱体生长、切板横向推入、正文分段渐入：接近 PPT 播放节奏，适合无人值守轮播', 'system', 1, 85)
;

-- ============================================================================
-- 核验（人工执行，不参与建库）
--   SELECT code, name, is_builtin, JSON_VALID(style_config) FROM adh_vis_components
--     WHERE category = 'motion_preset' ORDER BY sort_order;
--   预期 6 行、JSON_VALID 全为 1（解析端按白名单裁剪，JSON 损坏会整条回落默认档，
--   表现为「选了预设没反应」）。
--
--   悬空效果名自检（应返回 0 行；与 lib/motion.ts 的枚举清单同口径）：
--   SELECT code FROM adh_vis_components
--     WHERE category='motion_preset' AND (
--       JSON_UNQUOTE(JSON_EXTRACT(style_config,'$.enter.effect')) NOT IN
--         ('none','fadeUp','fade','slideDown','slideLeft','scale','blur','wipeDown')
--       OR JSON_UNQUOTE(JSON_EXTRACT(style_config,'$.chart.appear')) NOT IN
--         ('default','none','fadeIn','pathIn','growInY','waveIn','zoomIn')
--       OR JSON_UNQUOTE(JSON_EXTRACT(style_config,'$.transition.effect')) NOT IN
--         ('none','fadeIn','pushLeft','zoomIn','wipeDown')
--       OR JSON_UNQUOTE(JSON_EXTRACT(style_config,'$.reveal.effect')) NOT IN
--         ('none','fadeIn','sectionStagger'));
--   防线用例：frontend/src/lib/__tests__/motion.test.ts（枚举与迁移 seed 双向核对）。
-- ============================================================================

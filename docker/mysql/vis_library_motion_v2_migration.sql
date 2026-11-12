-- ============================================================
-- 内置动效预设第二轮校准（修复"预设之间看不出差别"）
-- ============================================================
-- 背景：vis_library_motion_migration.sql 首版把五档非默认预设都做成了 fadeUp 的时长变体
-- （位移 10~18px、时长 220~700ms），肉眼分不出来；而 G2 的 pathIn/growInY 反而最抢眼，
-- 用户体感就是"效果都在图表里、卡片没有出场"。现已把每档换成不同入场通道：
--   mp_soft → blur（模糊渐显，不靠位移）
--   mp_crisp → scale（快速放大落位）
--   mp_presentation → pop（弹入带一拍回弹，错峰拉开到 1.1s）
--   mp_none / mp_tech_flow / mp_default 不动（默认档改不得）。
--
-- 口径：
--   1. 只校准**入场通道仍是首版那条**的内置行（用 JSON_EXTRACT 比通道名，不做整坨 JSON
--      逐字比对：库里存的是带空格的写法，整坨比对永远不命中 → 会变成静默 0 行的假迁移） —— 管理员若已在管理页改过某档，本迁移不动它
--      （否则重跑迁移会静默吃掉人工编辑，属"幂等覆盖别人的值"那类事故）；
--   2. 新库直接跑修订后的 vis_library_motion_migration.sql 即为新值，本文件对其为空操作；
--   3. 只改 style_config 与 description，不动 code/name/category/status/is_enabled/sort_order；
--   4. 可安全重跑：更新过一次后条件不再命中。

SET NAMES utf8mb4;

-- mp_soft：fadeUp 变体 → blur
UPDATE `adh_vis_components` SET
  `style_config` = CAST('{"enter":{"effect":"blur","durationMs":700,"easing":"standard","staggerMs":140,"maxStaggerMs":700,"offsetPx":16},"chart":{"appear":"fadeIn","durationMs":700,"easing":"standard"},"transition":{"effect":"fadeIn","durationMs":700,"easing":"standard"},"reveal":{"effect":"fadeIn","staggerMs":140}}' AS JSON),
  `description` = '卡片由模糊渐显（不靠位移，700ms 慢一档）、图表与切板淡入：汇报讲解场景，观感沉稳不抢戏'
WHERE `category` = 'motion_preset' AND `source` = 'system' AND `is_builtin` = 1 AND `code` = 'mp_soft'
  AND JSON_UNQUOTE(JSON_EXTRACT(`style_config`, '$.enter.effect')) = 'fadeUp';

-- mp_crisp：fade 变体 → scale
UPDATE `adh_vis_components` SET
  `style_config` = CAST('{"enter":{"effect":"scale","durationMs":180,"easing":"snappy","staggerMs":0,"maxStaggerMs":0,"offsetPx":0},"chart":{"appear":"none","durationMs":0,"easing":"linear"},"transition":{"effect":"fadeIn","durationMs":200,"easing":"snappy"},"reveal":{"effect":"none","staggerMs":0}}' AS JSON),
  `description` = '卡片快速放大落位 0.18s、无错峰无位移、图表不做动画：数据密集型工作台，逐次交互不等人'
WHERE `category` = 'motion_preset' AND `source` = 'system' AND `is_builtin` = 1 AND `code` = 'mp_crisp'
  AND JSON_UNQUOTE(JSON_EXTRACT(`style_config`, '$.enter.effect')) = 'fade';

-- mp_presentation：slideDown 变体 → pop（错峰拉开，逐块播报才看得出来）
UPDATE `adh_vis_components` SET
  `style_config` = CAST('{"enter":{"effect":"pop","durationMs":460,"easing":"standard","staggerMs":180,"maxStaggerMs":1100,"offsetPx":28},"chart":{"appear":"growInY","durationMs":700,"easing":"standard"},"transition":{"effect":"pushLeft","durationMs":550,"easing":"standard"},"reveal":{"effect":"sectionStagger","staggerMs":160}}' AS JSON),
  `description` = '卡片逐块弹入落位（带一拍回弹，错峰 180ms 到 1.1s）、柱体生长、切板横向推入、正文分段渐入：接近 PPT 播放节奏，适合无人值守轮播'
WHERE `category` = 'motion_preset' AND `source` = 'system' AND `is_builtin` = 1 AND `code` = 'mp_presentation'
  AND JSON_UNQUOTE(JSON_EXTRACT(`style_config`, '$.enter.effect')) = 'slideDown';

-- 验证：三档应分别落到 blur / scale / pop，且六档入场通道互不相同
-- SELECT `code`, JSON_UNQUOTE(JSON_EXTRACT(`style_config`, '$.enter.effect')) AS enter_effect,
--        JSON_UNQUOTE(JSON_EXTRACT(`style_config`, '$.enter.durationMs')) AS dur,
--        JSON_UNQUOTE(JSON_EXTRACT(`style_config`, '$.enter.staggerMs')) AS stagger
--   FROM `adh_vis_components` WHERE `category` = 'motion_preset' AND `is_deleted` = 0 ORDER BY `sort_order`;

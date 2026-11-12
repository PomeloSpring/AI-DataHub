-- 出厂报告模板版式契约化：4 个内置 report_template 从「Jinja2 正文」升级为「可校验版式」
--
-- 为什么要改（现库核验的实际缺陷）：旧正文里没有静态章节，生成侧按
-- `report_service.extract_report_layout` 抽骨架时把 Jinja 变量与 emoji 当成了章节标题，
-- 实际抽出的是 `{{ date }} 数据日报` / `✅ {{ r.title }}` / `❌ {{ r.title }}`
-- （接口异常检测那份还抽出 `{问题标题}`、`一、{第一个分析维度}`）。注入给模型的版式契约要求
-- 「章节标题逐字保留」，事后 `layout_deviation` 按字符串精确比对 ⇒ 正常报告永远不可能命中
-- ⇒ 每次产出都被判"缺少章节"、退回通用事实表格（layout_result=fact_only）——
-- 即"选了模板反而必然退化"，而报表看起来只是"版式朴素"，没人会怀疑到模板上。
-- 另外：emoji/装饰图标本身违反 report-delivery 技能第一节的风格基线；平台已无任何 Jinja2
-- 渲染器（模板只解析成版式契约），旧正文其实是死重量。
--
-- 处置：显式登记 layout（静态业务章节，一律纯中文以免空格歧义误判缺章节），content 换成同口径
-- 骨架（rows 恒空 —— 模板资产不带任何数据），theme 不锁定（跟随查看者主题）。图表规格一律不
-- 编造：需要带图表的版式，请在报表中心把好报表「另存为模板」沉淀（宁缺勿错）。
--
-- 幂等：按 code upsert（uk_vis_component_code）。存量行只刷 name/style_config/description，
-- 不动 is_active/is_builtin —— 管理员下架过的模板不会被这次迁移重新上架。
--
-- 编辑注意：下面的 style_config 是内联 JSON 字面量，反斜杠成对出现（\\n）是正确的，
-- MySQL 解析后落到库里才是 JSON 需要的 \n。手工"顺手改成单根斜杠"会静默写坏 JSON。


USE adh2;

-- 日报模板（rpt_1）：4 个静态章节；无图表规格（不编造 columns）
INSERT INTO adh_vis_components
  (code, name, category, style_config, description, source,
   workspace_id, is_active, is_builtin, sort_order, created_at, updated_at)
VALUES ('rpt_1', '日报模板', 'report_template', '{"format": "markdown", "theme": "", "layout": {"format": "markdown", "headings": ["核心结论", "关键指标", "异常与风险", "数据口径说明"], "charts": []}, "content": "# 报告标题\\n\\n## 核心结论\\n\\n（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）\\n\\n## 关键指标\\n\\n（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）\\n\\n## 异常与风险\\n\\n（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）\\n\\n## 数据口径说明\\n\\n（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）\\n"}',
        '日报版式：核心结论 / 关键指标 / 异常与风险 / 数据口径说明。只锁结构与主题，数值每次由本轮取数结果重新产出。', 'system', 0, 1, 1, 201, NOW(), NOW())
ON DUPLICATE KEY UPDATE
  name = VALUES(name), style_config = VALUES(style_config),
  description = VALUES(description), updated_at = NOW();

-- 周报模板（rpt_2）：4 个静态章节；无图表规格（不编造 columns）
INSERT INTO adh_vis_components
  (code, name, category, style_config, description, source,
   workspace_id, is_active, is_builtin, sort_order, created_at, updated_at)
VALUES ('rpt_2', '周报模板', 'report_template', '{"format": "markdown", "theme": "", "layout": {"format": "markdown", "headings": ["本周概览", "趋势变化", "问题与改进", "下周关注"], "charts": []}, "content": "# 报告标题\\n\\n## 本周概览\\n\\n（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）\\n\\n## 趋势变化\\n\\n（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）\\n\\n## 问题与改进\\n\\n（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）\\n\\n## 下周关注\\n\\n（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）\\n"}',
        '周报版式：本周概览 / 趋势变化 / 问题与改进 / 下周关注。只锁结构与主题，数值每次由本轮取数结果重新产出。', 'system', 0, 1, 1, 202, NOW(), NOW())
ON DUPLICATE KEY UPDATE
  name = VALUES(name), style_config = VALUES(style_config),
  description = VALUES(description), updated_at = NOW();

-- HTML 日报模板（rpt_3）：4 个静态章节；无图表规格（不编造 columns）
INSERT INTO adh_vis_components
  (code, name, category, style_config, description, source,
   workspace_id, is_active, is_builtin, sort_order, created_at, updated_at)
VALUES ('rpt_3', 'HTML 日报模板', 'report_template', '{"format": "html", "theme": "", "layout": {"format": "html", "headings": ["核心结论", "关键指标", "异常与风险", "数据口径说明"], "charts": []}, "content": "<!doctype html>\\n<html lang=\\"zh-CN\\">\\n<head><meta charset=\\"utf-8\\">\\n<title>报告标题</title></head>\\n<body>\\n<h2>核心结论</h2>\\n<p>（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）</p>\\n<h2>关键指标</h2>\\n<p>（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）</p>\\n<h2>异常与风险</h2>\\n<p>（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）</p>\\n<h2>数据口径说明</h2>\\n<p>（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）</p>\\n</body>\\n</html>"}',
        '日报版式的 HTML 形态（适合邮件推送），章节与「日报模板」一致，只锁结构不锁数据。', 'system', 0, 1, 1, 203, NOW(), NOW())
ON DUPLICATE KEY UPDATE
  name = VALUES(name), style_config = VALUES(style_config),
  description = VALUES(description), updated_at = NOW();

-- 每日接口异常检测报告（rpt_20260703001）：5 个静态章节；无图表规格（不编造 columns）
INSERT INTO adh_vis_components
  (code, name, category, style_config, description, source,
   workspace_id, is_active, is_builtin, sort_order, created_at, updated_at)
VALUES ('rpt_20260703001', '每日接口异常检测报告', 'report_template', '{"format": "html", "theme": "", "layout": {"format": "html", "headings": ["核心发现", "请求量统计", "异常访问检测", "错误日志分析", "风险与建议"], "charts": []}, "content": "<!doctype html>\\n<html lang=\\"zh-CN\\">\\n<head><meta charset=\\"utf-8\\">\\n<title>报告标题</title></head>\\n<body>\\n<h2>核心发现</h2>\\n<p>（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）</p>\\n<h2>请求量统计</h2>\\n<p>（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）</p>\\n<h2>异常访问检测</h2>\\n<p>（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）</p>\\n<h2>错误日志分析</h2>\\n<p>（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）</p>\\n<h2>风险与建议</h2>\\n<p>（本节写：一句话结论 + 支撑数据；数值一律取本轮查询结果）</p>\\n</body>\\n</html>"}',
        '接口请求量异常检测版式：核心发现 / 请求量统计 / 异常访问检测 / 错误日志分析 / 风险与建议。只锁结构与主题，数值每次由本轮取数结果重新产出。', 'system', 0, 1, 1, 204, NOW(), NOW())
ON DUPLICATE KEY UPDATE
  name = VALUES(name), style_config = VALUES(style_config),
  description = VALUES(description), updated_at = NOW();

-- 核验 1（结构）：期望 4 行 headings 均为静态业务章节名，不含 '{{' / '{%' / '}' / emoji
--   SELECT code,
--          JSON_UNQUOTE(JSON_EXTRACT(style_config,'$.format'))      AS fmt,
--          JSON_EXTRACT(style_config,'$.layout.headings')           AS headings,
--          IFNULL(JSON_LENGTH(JSON_EXTRACT(style_config,'$.layout.charts')),0) AS charts
--     FROM adh_vis_components WHERE category='report_template' ORDER BY sort_order;
-- 核验 2（JSON 真的可解析）：上面 JSON_EXTRACT 出 NULL 就说明字面量转义被写坏了。

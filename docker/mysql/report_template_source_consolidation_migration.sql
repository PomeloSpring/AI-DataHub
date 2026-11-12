-- 报告模板真值源收敛：遗留表 adh_report_templates 退役为冻结表（幂等，可重复执行）
--
-- 背景（本批修复的根因）：平台曾并存两套"报告模板"——
--   ① `adh_report_templates`（`/api/scheduled-tasks/templates` + `/api/report-templates` + `_get_template_content`）；
--   ② UI 字模库 `adh_vis_components(category='report_template')`（配置页与任务表单实际读的那一份）。
-- 生成侧（executor）过去两套都不读，2026-11 起只认 ②：模板解析成一个「版式契约」
-- （`report_service.resolve_report_template`），定时任务、报表「套用模板 / 另存为模板」
-- 与「用最新数据重新生成」共用同一份口径。
--
-- 处置：② 为唯一事实源；① 的 REST 端点、service CRUD 与 `_get_template_content` 已删除，
-- 表本身保留结构与存量行（不 DROP，历史数据可查回）。任何新代码不得再消费本表；
-- 若要彻底清理，请单独决策并先导出存量行。

USE adh2;

ALTER TABLE adh_report_templates
    COMMENT = '已退役为冻结表：报告模板唯一事实源 = adh_vis_components(category=report_template)（版式契约），本表无任何读写路径消费，仅保留历史行';

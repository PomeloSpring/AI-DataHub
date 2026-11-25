-- 质量概览：删除质量报告 / 问题记录的方法集补齐（2026-11-25）
--
-- 为什么需要：
--   质量概览新增「删除质量报告」（DELETE /api/quality/reports/{id}）与「删除问题记录」
--   （POST /api/quality/results/batch-delete、DELETE /api/quality/results/{id}）。
--   permission_model_migration.sql 里 quality:manage 的 api_method 只有 POST，
--   DELETE 请求会被 API 权限中间件拦成 403（admin 旁路掩盖了问题）——删除按钮等于死开关。
--   质量规则的编辑/删除（PUT/DELETE /api/quality/rules/{id}）同一前缀，一并覆盖。
-- 口径：
--   写动作统管在 quality:manage（读=quality:read），不新增权限码、不新增菜单；
--   删除报告归「质量概览」菜单（data:quality），删除问题记录同源。
-- 幂等：已是 POST,PUT,DELETE 时 0 变更，可重复执行（当前库已是该口径，重跑 0 行）。
-- 核验：SELECT perm_code, api_pattern, api_method FROM adh_perm_registry
--       WHERE perm_code = 'quality:manage';

UPDATE adh_perm_registry
   SET api_method = 'POST,PUT,DELETE'
 WHERE perm_code = 'quality:manage'
   AND api_method <> 'POST,PUT,DELETE';
-- ============================================================================
-- 权限收窄迁移：4 组宽模式 + 本体 DELETE 落点 + eval 菜单登记 + taxonomy 拆码
--              + 空承诺 write 钉回 none + 孤儿授权清理
-- ============================================================================
-- 背景（2026-11-28 角色权限体检，探针实证；中间件 union 裁决 = 任一持码的
-- pattern 命中即放行，宽 pattern 会把同前缀其它码的专属动作一并放行）：
--   1) catalog:manage 的 /api/catalog*（写方法）把本体域专属端点一并放行——持
--      「元数据管理」的角色可绕过 ontology:publish/save/activate 发布/保存/激活本体；
--      UI 按窄码裁剪按钮 → 问题静默潜伏，只有直调 API 才触发（授权 > 授权意图）。
--   2) report:manage 的 /api/reports* 覆盖 report-template:manage 的 /api/reports/templates*；
--   3) quality:manage 的 /api/quality* 覆盖 quality-rules:manage 的 /api/quality/rules*；
--   4) eval:manage 的 /api/eval* 覆盖 eval:gate 的 /api/eval/gate*（门禁阈值可被放宽）。
--   5) 本体模型/草案 DELETE 此前只被 catalog:manage 宽模式兜住（本体码无 DELETE 落点，
--      前端删除按钮因此按 catalog:manage 显隐）；本次把 DELETE 并入 ontology:save
--      （api_method PUT→PUT,DELETE），前端同批换成 hasPerm('ontology:save')，
--      tests/test_ontology_perm_binding.py 的 DECLARED_UNOWNED_ROUTES 同批撤销该例外。
--   6) eval:gate/manage/read 三码 menu_key 为空且菜单表无 eval 行：侧栏 /system/eval
--      入口常驻（SystemLayout，menu.json system.eval=评测中心）但菜单显隐反查永远空
--      →「API 开放、导航不可见」的半开放状态。本次登记菜单 + 三码归属 system:eval。
--      迁移前可见现状 = 仅 admin（admin 旁路），故不做角色 INSERT（维持现状不扩权），
--      其它角色由管理员在角色页显式勾选。
--   7) content:taxonomy（栏目与标签）此前显隐与写面都挂 article:manage：角色页渲染
--      不出独立分组，管理员无法单独授/收。本次拆专属码 taxonomy:manage（categories/
--      tags 读写面），菜单 perm_code 切换 + 存量 article:manage 持有者回填（不回填 =
--      存量用户「栏目与标签」入口凭空消失）。article:manage 的 pattern **不收窄**：
--      内容创作里选栏目/打标签仍走它（同域内的有意重叠，非跨域放大）。
--   8) 14 个 write 级空承诺（ai_channel=none：无 require_write_perm 调用点、无
--      FUNCTION_ACTION_SPECS 动作）钉回 none：write 是休眠雷，将来任何一次接线当天
--      自动生效、绕过「该不该对 AI 开放」的显式决策。收敛方向与
--      test_function_ai_access.KNOWN_NO_CHANNEL_PERMS 的注释一致：改回 none。
--      确需开放：同批补 PERM_GATED_ACTION_PERMS 登记或 FUNCTION_ACTION_SPECS 安全
--      投影并配用例，再在角色页显式抬级。
--   9) 孤儿授权清理：adh_role_perms 存在 role_id 不在 adh_roles 的行（角色删除未级联），
--      fail-closed 下不可达，但会污染授权统计与问题排查（考古误导）。
-- 幂等性：UPDATE 幂等；INSERT 走 ON DUPLICATE KEY UPDATE / INSERT IGNORE
--         （adh_menu_registry.menu_key 与 uk_role_perm(role_id,perm_code) 唯一键）；
--         可重复执行，二次执行 0 变更。不含 USE（库由连接配置决定）。
-- 应用后核验：目标行 pattern/级别、覆盖矩阵（每个专属端点的 owner 集合恰等于其专属码）、
--         菜单显隐反查模拟（存量可见现状不变）。
-- ============================================================================

-- ── 1. 孤儿授权清理（先清，后面的回填只面向真实角色）──────────────────────────
DELETE FROM adh_role_perms
WHERE role_id NOT IN (SELECT id FROM adh_roles);

-- ── 2. catalog:manage 收窄：元数据写面 = 数据产品 + /api/admin/metadata ──────
--    本体写端点全部归 ontology:* / kb:manage 专属码（覆盖核对清单见迁移注释头部与
--    temp 复测探针的覆盖矩阵断言）。
UPDATE adh_perm_registry
SET api_pattern = '/api/catalog/data-products*,/api/admin/metadata*'
WHERE perm_code = 'catalog:manage';

-- ── 3. ontology:save 补 DELETE 落点（模型/草案删除与「保存本体」同码）─────────
--    同码原则：前端删除按钮 hasPerm('ontology:save')（OntologyModeling.tsx 同批换）；
--    DELETE 命中 /api/catalog/ontology/models/{id} 与 .../models/{id}/draft 两条路由。
UPDATE adh_perm_registry
SET api_method = 'PUT,DELETE'
WHERE perm_code = 'ontology:save' AND api_method = 'PUT';

-- ── 4. report:manage 收窄：模板面归 report-template:manage ───────────────────
--    [0-9]* 是 fnmatch 字符类（api_permission._match_path 用 fnmatch.fnmatch）：
--    匹配数字 id 开头的报告子路径（report_id 为 integer，openapi 已核），
--    不匹配 /api/reports/templates*（首字符 't' 非数字）。精确条目覆盖
--    POST /api/reports（新建）与 POST /api/reports/generate。
UPDATE adh_perm_registry
SET api_pattern = '/api/reports,/api/reports/generate,/api/reports/[0-9]*'
WHERE perm_code = 'report:manage';

-- ── 5. quality:manage 收窄：规则面归 quality-rules:manage ────────────────────
--    触发检核（execute）、质量报告生成/删除、结果清理归 quality:manage；
--    规则 CRUD 与规则执行归 quality-rules:manage（/api/quality/rules*）。
UPDATE adh_perm_registry
SET api_pattern = '/api/quality/execute,/api/quality/reports*,/api/quality/results*'
WHERE perm_code = 'quality:manage';

-- ── 6. eval:manage 收窄：门禁面归 eval:gate ──────────────────────────────────
--    用例 CRUD（cases* 含 /active）与触发评测运行（POST /api/eval/runs）归 manage；
--    门禁阈值读写删只归 eval:gate（门禁不得被管理档连带放宽）。
UPDATE adh_perm_registry
SET api_pattern = '/api/eval/cases*,/api/eval/runs'
WHERE perm_code = 'eval:manage';

-- ── 7. eval 菜单登记 + 三码归属（menu-permission-sync §2）────────────────────
--    与侧栏一致：key /system/eval → menu_key 'system:eval'，section=运维管理，
--    label=评测中心（menu.json system.eval），排序在 observability(20) 与 monitoring(22) 之间。
--    菜单显隐码用读档 eval:read；域不对 AI 开放（三码 ai_access 已是 none，防自评自证）。
INSERT INTO adh_menu_registry (menu_key, label, section, module, sort, perm_code, ai_access)
VALUES ('system:eval', '评测中心', '运维管理', 'system', 21, 'eval:read', 'none')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), section = VALUES(section), module = VALUES(module),
  sort = VALUES(sort), perm_code = VALUES(perm_code), ai_access = VALUES(ai_access),
  is_active = 1;

UPDATE adh_perm_registry
SET menu_key = 'system:eval'
WHERE perm_code IN ('eval:gate', 'eval:manage', 'eval:read')
  AND (menu_key IS NULL OR menu_key = '');

-- ── 8. taxonomy:manage 专属码 + 菜单切换 + 存量回填 ──────────────────────────
--    栏目与标签是受控词表（服务端拒词表外标签，改名走级联），管理面独立成码；
--    ai_access=none：词表改名级联影响全部内容条目的栏目口径，不开放给 AI
--    （AI 侧只读清单已由 article:manage 的 content_taxonomy.list 承担）。
INSERT INTO adh_perm_registry
    (perm_code, label, module, module_label, description, api_pattern, api_method,
     menu_key, sort, is_active, ai_access, ai_action_key, ai_note)
VALUES
    ('taxonomy:manage', '管理栏目与标签', 'content', '内容中心',
     '栏目/标签词表的新建、改名（级联）、启停与删除；内容创作中的选择/打标签走 article:manage',
     '/api/content/categories*,/api/content/tags*', 'GET,POST,PUT,DELETE',
     'content:taxonomy', 10, 1, 'none', '',
     '受控词表改名会级联影响全部内容条目的栏目口径，不开放给 AI（防自改口径）；AI 侧只读清单走 article:manage 的 content_taxonomy.list')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), module = VALUES(module), module_label = VALUES(module_label),
  description = VALUES(description), api_pattern = VALUES(api_pattern),
  api_method = VALUES(api_method), menu_key = VALUES(menu_key), sort = VALUES(sort),
  is_active = 1, ai_access = VALUES(ai_access), ai_action_key = '',
  ai_note = VALUES(ai_note);

-- 菜单显隐切换到专属码（谁本来能看见 = article:manage 持有者 → 下面回填保持现状）
UPDATE adh_menu_registry
SET perm_code = 'taxonomy:manage'
WHERE menu_key = 'content:taxonomy';

-- 存量回填（维持迁移前入口可见现状；管理员仍可按角色收回；只面向真实角色）
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT DISTINCT rp.role_id, 'taxonomy:manage', NOW()
FROM adh_role_perms rp
JOIN adh_roles r ON r.id = rp.role_id
WHERE rp.perm_code = 'article:manage';

-- ── 9. write 级空承诺钉回 none（休眠防护）────────────────────────────────────
UPDATE adh_perm_registry
SET ai_access = 'none',
    ai_note = '写面未接入 AI 通道（无 require_write_perm 调用点与功能动作登记），按休眠防护钉回 none；确需开放须同批补 PERM_GATED_ACTION_PERMS 登记或 FUNCTION_ACTION_SPECS 安全投影并配用例，再在角色页显式抬级'
WHERE perm_code IN (
  'catalog:manage', 'dataset:query', 'graph:manage', 'kb:manage',
  'playground:execute', 'quality-review:manage', 'quality-rules:manage',
  'report-template:manage', 'scheduled:execute', 'standard:manage',
  'tags:manage', 'task-monitor:manage', 'vis:manage', 'workspace:manage'
) AND ai_access = 'write';

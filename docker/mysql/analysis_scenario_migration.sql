-- ============================================================================
-- 「分析场景」（原「快速路径」）迁到数据中台 + 语义层权限码补 api_pattern
--
-- 用户 2026-11-04 拍板:
--   1) 知识库**管理**留在系统配置(system:knowledge-base 不动); 知识库的**查看与编辑**
--      进内容中心(见 kb_browse_migration.sql);
--   2) 快速路径属"改了它会改变 AI 取数行为"的语料, 归**数据中台语义层**, 不属内容中心;
--   3) 「快速路径」这个名字不合适 —— 实质是**用 markdown 描述的场景化查询步骤**
--      (对已建模的对象/指标/维度的编排, 相当于针对本系统的定制版 skill),
--      因此用户可见名改为**分析场景**。它与 SQL 示例对、数据集都不是一回事。
--
-- 为什么不顺手把 knowledge_type 枚举值也改掉:
--   `quick_path` 被 4 处消费方读取(quick_paths.match / Chat / Home / 管理页), 改值要
--   连带动数据和这些读取点, 收益只是"名字统一", 风险是漏改一处就静默丢语料。
--   口径: **内部枚举值保持 quick_path, 用户可见名 = 分析场景**。
--
-- 本批同时修两个静默存量缺陷:
--   A. knowledge:read/manage 与 sql-pairs:read/manage 的 api_pattern 是空串。
--      中间件按 api_pattern × api_method 交叉匹配, 空串永远匹配不上 → 非 admin 角色
--      即使勾了这些码也一律 403(实测 analyst 勾了 sql-pairs 仍打不开 SQL 示例对页)。
--   B. 管理面 /api/admin/knowledge* 同时服务四种 knowledge_type, 而两类归两个码
--      (quick_path→knowledge:*, sql_pair→sql-pairs:*)。api_pattern 只能按路径裁决、
--      做不了"按查询参数分类型", 因此**类型级归属由路由层再校一遍**(见
--      backend/modules/auth/api/knowledge.py 的 _assert_type_perm)。
--      这不是第二套"谁能做什么"的判断: 路径能不能调仍由中间件裁决, 路由层只补
--      "是哪一类对象"。
--
-- 幂等: 全部 INSERT ... ON DUPLICATE KEY UPDATE / UPDATE / DELETE, 可重复执行。
-- ============================================================================
USE adh2;

-- ----------------------------------------------------------------------------
-- 1) 菜单迁移: system:knowledge-management → data:scenario(语义层, sort 8)
--    menu_key 是主键, 改名必须"删旧插新", 不能只 UPDATE 留下两行。
-- ----------------------------------------------------------------------------
DELETE FROM adh_menu_registry WHERE menu_key = 'system:knowledge-management';

INSERT INTO adh_menu_registry (menu_key, label, section, module, sort, perm_code, ai_access, is_active) VALUES
  ('data:scenario', '分析场景', '语义层', 'data', 8, 'knowledge:read', 'read', 1)
ON DUPLICATE KEY UPDATE
  label = VALUES(label), section = VALUES(section), module = VALUES(module),
  sort = VALUES(sort), perm_code = VALUES(perm_code), ai_access = VALUES(ai_access), is_active = 1;

-- 语义层内顺序与 DataPlatformLayout 侧栏数组一致(menu-permission-sync §2.1):
--   本体建模(5) → 建模待办(6) → SQL 示例对(7) → 分析场景(8)

-- ----------------------------------------------------------------------------
-- 2) 权限点: 改 label + 迁 menu_key + 补 api_pattern(修缺陷 A)
-- ----------------------------------------------------------------------------
INSERT INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note) VALUES
  ('knowledge:read', '查看分析场景', 'data', '数据中台',
   '查看分析场景清单(原「快速路径」; 内部枚举值仍为 quick_path, 用户可见名为分析场景)',
   '/api/admin/knowledge,/api/admin/knowledge/*', 'GET',
   'data:scenario', 118, 1, 'read', '',
   '场景正文会被注入系统提示词影响取数, AI 侧只读不可改'),
  ('knowledge:manage', '编辑分析场景', 'data', '数据中台',
   '新建/修改/停用分析场景。场景是对象/指标/维度的场景化编排, 改了会改变 AI 取数行为',
   '/api/admin/knowledge,/api/admin/knowledge/*', 'POST,PUT,PATCH,DELETE',
   'data:scenario', 119, 1, 'write', '',
   '写动作按权限码直执行; 类型级归属由路由层 _assert_type_perm 复核')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), module = VALUES(module), module_label = VALUES(module_label),
  description = VALUES(description), api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
  menu_key = VALUES(menu_key), sort = VALUES(sort), ai_access = VALUES(ai_access),
  ai_note = VALUES(ai_note), is_active = 1;

-- SQL 示例对: 原本就有码但 api_pattern 为空 → 勾了也 403(缺陷 A 第二处)
UPDATE adh_perm_registry
SET api_pattern = '/api/admin/knowledge,/api/admin/knowledge/*',
    api_method  = CASE WHEN perm_code LIKE '%:read' THEN 'GET' ELSE 'POST,PUT,PATCH,DELETE' END,
    description = '查看/编辑 SQL 示例对(含物理表列名与 SQL 片段, 涉密域, 仅按码门控)'
WHERE perm_code IN ('sql-pairs:read', 'sql-pairs:manage');

-- 菜单 perm_code 以权限点为真值源回填(防两侧飘移)
UPDATE adh_menu_registry m
  JOIN adh_perm_registry p ON p.menu_key = m.menu_key AND p.perm_code = 'knowledge:read'
SET m.perm_code = p.perm_code
WHERE m.menu_key = 'data:scenario';

-- ----------------------------------------------------------------------------
-- 3) 存量角色可见现状
--    admin 由中间件天然放行; analyst 已持有 sql-pairs:*, 补完 pattern 后它的
--    SQL 示例对页从"勾了打不开"变成真能用 —— 这是修复不是扩权。
--    本批不给任何角色新增 knowledge:read/manage: 分析场景是写能力, 由管理员显式配置。
-- ----------------------------------------------------------------------------
SELECT 1;

-- ----------------------------------------------------------------------------
-- 4) 核验(应用后人工确认, 勿删)
-- ----------------------------------------------------------------------------
-- SELECT menu_key,label,section,module,sort,perm_code FROM adh_menu_registry
--   WHERE module='data' AND section='语义层' ORDER BY sort;
-- SELECT perm_code,label,menu_key,api_pattern,api_method,ai_access FROM adh_perm_registry
--   WHERE perm_code IN ('knowledge:read','knowledge:manage','sql-pairs:read','sql-pairs:manage');
-- 旧菜单行必须已消失(留着就是死开关):
-- SELECT COUNT(*) FROM adh_menu_registry WHERE menu_key='system:knowledge-management';

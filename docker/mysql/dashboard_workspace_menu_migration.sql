-- ============================================================================
-- 数据看板收进工作空间: 角色权限「菜单与功能」同步登记
--
-- 背景:
--   * 数据看板已从一级模块收进工作空间侧栏(默认消费页), 看板编辑只保留在
--     系统配置「看板管理」(工作空间侧不提供编辑);
--   * 角色权限 → 菜单与功能 的菜单登记需与新导航一致: 补「数据看板」,
--     并把「查看看板」权限点归位到消费菜单(系统配置「看板管理」只留「管理看板」)。
--
-- 口径:
--   * 菜单显隐 = 权限码反查(adh_menu_registry.perm_code ⋈ adh_role_perms),
--     数据看板入口与智能问数/报表中心同口径参与角色配置(不再硬编码恒可见);
--   * 内容级可见性不变: 仍由 adh_role_dashboard_access fail-closed 裁决;
--   * 看板写权限不变: 仅看板属主/管理员(check_dashboard_editable)。
--
-- 幂等: 全部 INSERT ... ON DUPLICATE KEY UPDATE / INSERT IGNORE, 可重复执行。
-- ============================================================================
USE adh;

-- 1) 工作空间菜单补登记: 数据看板(消费入口, 侧栏「数据分析」组第一项)
INSERT INTO adh_menu_registry (menu_key, label, section, module, sort, perm_code, ai_access) VALUES
  ('workspace:dashboards', '数据看板', '数据分析', 'workspace', 1, 'dashboard:read', 'read')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), section = VALUES(section), module = VALUES(module),
  sort = VALUES(sort), perm_code = VALUES(perm_code), ai_access = VALUES(ai_access), is_active = 1;

-- 2) 数据分析组内排序与侧栏一致: 数据看板(1) → 智能问数(2) → 报表中心(3);
--    任务调度随「自动化」组顺延到 4
UPDATE adh_menu_registry SET sort = 2 WHERE menu_key = 'workspace:chat';
UPDATE adh_menu_registry SET sort = 3 WHERE menu_key = 'workspace:reports';
UPDATE adh_menu_registry SET sort = 4 WHERE menu_key = 'workspace:scheduled';

-- 3) 「查看看板」归位到消费菜单; 系统配置「看板管理」只留「管理看板」
UPDATE adh_perm_registry
   SET menu_key = 'workspace:dashboards', module = 'workspace', module_label = '工作空间'
 WHERE perm_code = 'dashboard:read';
UPDATE adh_perm_registry SET menu_key = 'system:dashboards' WHERE perm_code = 'dashboard:manage';

-- 4) 菜单权限码回填(与既有迁移同口径: 菜单行缺失 perm_code 时从权限点反查)
UPDATE adh_menu_registry mr
  JOIN adh_perm_registry p ON p.menu_key = mr.menu_key
  SET mr.perm_code = p.perm_code
  WHERE p.menu_key <> '' AND (mr.perm_code IS NULL OR mr.perm_code = '');

-- 5) 默认授权: 数据看板是日常消费入口 —— 全部既有角色默认获得「查看看板」,
--    维持迁移前的入口可见现状; 管理员仍可按角色收回(入口随之隐藏)。
INSERT IGNORE INTO adh_role_perms (role_id, perm_code)
  SELECT id, 'dashboard:read' FROM adh_roles;

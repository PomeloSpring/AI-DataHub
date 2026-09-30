-- 数据安全配置菜单聚合迁移
-- 将「敏感数据」与「RLS 行级安全」统一归入数据中台「数据安全配置」分组:
--   1) data:sensitive 从「数据质量」移入「数据安全配置」
--   2) 新增 data:rls 菜单，system:rls 退役(is_active=0)，旧路由 /system/rls 由前端重定向
--   3) 权限点 rls:read / rls:manage 归属改到数据中台(API pattern 不变, 接口鉴权不受影响)
-- 幂等, 可重复执行。仅迁移归属, 不新增/收回任何角色授权。

USE adh;

-- 1. 敏感数据移入数据安全配置分组
UPDATE adh_menu_registry SET section='数据安全配置', sort=11
  WHERE menu_key='data:sensitive' AND module='data';

-- 2. 新增 RLS 行级安全菜单(已存在则对齐)
INSERT INTO adh_menu_registry (menu_key, label, section, module, sort, perm_code) VALUES
  ('data:rls', 'RLS 行级安全', '数据安全配置', 'data', 12, 'rls:read')
ON DUPLICATE KEY UPDATE
  label=VALUES(label), section=VALUES(section), module=VALUES(module), sort=VALUES(sort);

-- 3. 数据同步段后移, 保持数据安全配置紧跟数据质量
UPDATE adh_menu_registry SET sort=13 WHERE menu_key='data:sync' AND module='data';
UPDATE adh_menu_registry SET sort=14 WHERE menu_key='data:sync/logs' AND module='data';

-- 4. 退役系统配置下的旧行级安全菜单(退出菜单注册表与权限反查)
UPDATE adh_menu_registry SET is_active=0, perm_code='' WHERE menu_key='system:rls';

-- 5. 权限点归属改到数据中台(api_pattern 不变, API 鉴权不受影响)
UPDATE adh_perm_registry SET module='data', module_label='数据中台', menu_key='data:rls'
  WHERE perm_code IN ('rls:read', 'rls:manage');

-- 6. 遗留角色-菜单授权平移(system:rls → data:rls)，保持权限对等
--    注：不迁移旧死表 adh_permissions(线上为早期 resource/action 结构，无代码读取)，
--    权限点唯一事实源是 adh_perm_registry。
INSERT IGNORE INTO adh_role_menus (role_id, menu_key, is_allowed)
  SELECT role_id, 'data:rls', is_allowed FROM adh_role_menus WHERE menu_key='system:rls';

-- 7. 回填菜单 perm_code(仅填空，与 datasets_migration 同口径)
UPDATE adh_menu_registry mr
  JOIN adh_perm_registry p ON p.menu_key = mr.menu_key
  SET mr.perm_code = p.perm_code
  WHERE p.menu_key <> '' AND (mr.perm_code IS NULL OR mr.perm_code = '');

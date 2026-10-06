-- ============================================================================
-- 权限码种子重置迁移 (幂等, 可重复执行)
--
-- 背景: 「菜单与功能」权限码分配(adh_role_perms)缺少初始化数据 —— 管理员
-- (admin, role_id=1) 无任何权限码行, 打开配置页全部显示未勾选。
-- 本迁移按默认推荐权限集重置系统角色(admin/analyst/viewer)的权限码:
--   * 会覆盖这些角色此前手工调整过的勾选(按"重置种子"决策执行);
--   * 自定义角色(data_viewer 等)保留现状不动;
--   * 清理 adh_role_perms 中指向不存在角色的幽灵残留(role_id 100/300)。
--
-- 幂等性(可重复执行): DELETE + INSERT 重放结果一致。
-- ============================================================================

-- 1. 清空系统角色的权限码分配(覆盖手工调整)
DELETE FROM adh_role_perms WHERE role_id IN (1, 2, 3);

-- 2. 管理员(admin, role_id=1) = 全部权限码 (admin 不受限, 勾选为展示一致性)
INSERT IGNORE INTO adh_role_perms (role_id, perm_code)
SELECT 1, perm_code FROM adh_perm_registry WHERE is_active = 1;

-- 3. 数据分析师(analyst, role_id=2) = 默认推荐集(分析读写, 40 码)
INSERT IGNORE INTO adh_role_perms (role_id, perm_code) VALUES
(2,'catalog:manage'),(2,'catalog:read'),(2,'chat:use'),
(2,'dashboard:manage'),(2,'dashboard:read'),
(2,'dataset:manage'),(2,'dataset:query'),(2,'dataset:read'),
(2,'datasource:manage'),(2,'datasource:read'),
(2,'glossary:read'),(2,'graph:read'),
(2,'kb:manage'),(2,'kb:read'),
(2,'lineage:read'),(2,'metrics:read'),
(2,'model:manage'),(2,'model:read'),
(2,'ontology:activate'),(2,'ontology:generate'),(2,'ontology:import'),
(2,'ontology:read'),(2,'ontology:save'),
(2,'playground:execute'),(2,'playground:read'),
(2,'quality:manage'),(2,'quality:read'),
(2,'report:manage'),(2,'report:read'),
(2,'scheduled:execute'),(2,'scheduled:manage'),(2,'scheduled:read'),
(2,'sensitive:manage'),(2,'sensitive:read'),
(2,'standard:manage'),(2,'standard:read'),
(2,'sync:execute'),(2,'sync:manage'),(2,'sync:read'),
(2,'tags:manage');

-- 4. 普通用户(viewer, role_id=3) = 默认推荐集(只读+取数+本体编辑, 19 码)
INSERT IGNORE INTO adh_role_perms (role_id, perm_code) VALUES
(3,'catalog:manage'),(3,'catalog:read'),(3,'chat:use'),
(3,'dataset:query'),(3,'dataset:read'),
(3,'glossary:read'),(3,'graph:read'),(3,'lineage:read'),(3,'metrics:read'),
(3,'ontology:activate'),(3,'ontology:generate'),(3,'ontology:import'),
(3,'ontology:read'),(3,'ontology:save'),
(3,'quality:manage'),(3,'quality:read'),
(3,'report:manage'),(3,'report:read'),
(3,'tags:manage');

-- 5. 清理幽灵残留: adh_role_perms 中指向不存在角色的行(如历史 demo 的 role_id 100/300)
DELETE rp FROM adh_role_perms rp
  LEFT JOIN adh_roles r ON r.id = rp.role_id
  WHERE r.id IS NULL;

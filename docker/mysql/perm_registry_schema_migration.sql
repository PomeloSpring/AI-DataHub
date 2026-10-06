-- ============================================================================
-- 权限码模型建表补齐迁移 (幂等)
--
-- 背景: adh_perm_registry / adh_role_perms 是现行权限码模型的真值源
-- (api_permission.py 中间件按码展开 api_pattern 门控, permissionStore 按码裁菜单),
-- 但全仓此前没有它们的建表迁移(存量库由历史操作建出)。
-- 本迁移仅补齐 CREATE TABLE IF NOT EXISTS, 不覆盖/不修改任何存量表与数据;
-- 种子数据仍由既有迁移维护(permission_model_migration.sql / datasets_migration.sql /
-- permission_pattern_fix.sql / data_security_menu_migration.sql 等)。
-- ============================================================================

-- 1. 权限点注册表: 接口绑定权限标识(perm_code), 菜单归属(menu_key), 按模块分组
CREATE TABLE IF NOT EXISTS adh_perm_registry (
  id INT AUTO_INCREMENT PRIMARY KEY,
  perm_code VARCHAR(64) NOT NULL UNIQUE COMMENT '权限标识, 如 ontology:save',
  label VARCHAR(128) NOT NULL COMMENT '权限名称',
  module VARCHAR(32) NOT NULL DEFAULT '' COMMENT '所属模块: system/data/workspace',
  module_label VARCHAR(64) DEFAULT '' COMMENT '模块中文名',
  description VARCHAR(256) DEFAULT '' COMMENT '口径说明',
  api_pattern VARCHAR(512) DEFAULT '' COMMENT '绑定API路径模式(*通配), 逗号分隔多值, 空=不做API层鉴权',
  api_method VARCHAR(32) DEFAULT '*' COMMENT 'API方法: GET/POST/PUT/DELETE, 逗号分隔多值',
  menu_key VARCHAR(128) DEFAULT '' COMMENT '关联菜单标识(adh_menu_registry.menu_key), 空=无对应菜单',
  sort INT DEFAULT 0,
  is_active TINYINT(1) DEFAULT 1,
  INDEX idx_module (module),
  INDEX idx_menu_key (menu_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限点注册表(接口绑定权限标识)';

-- 2. 角色权限码授权: 角色配置页「菜单与功能」保存目标 (全量替换, 空=清空全部功能权限)
CREATE TABLE IF NOT EXISTS adh_role_perms (
  id INT AUTO_INCREMENT PRIMARY KEY,
  role_id INT NOT NULL COMMENT '角色 ID (adh_roles.id)',
  perm_code VARCHAR(64) NOT NULL COMMENT '权限标识 (adh_perm_registry.perm_code)',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_perm (role_id, perm_code),
  INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色权限码授权';

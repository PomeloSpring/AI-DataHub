-- ============================================================================
-- 仪表盘可见性按角色授权迁移（幂等, 可重复执行）
--
-- 背景:
--   仪表盘"可看"范围原由工作空间菜单配置(adh_menu_items)决定, 现改为按用户
--   角色配置(adh_role_dashboard_access)。可见性唯一裁决 = 用户角色授权
--   (adh_user_roles ⋈ adh_role_dashboard_access, 经
--    role_service.get_user_allowed_dashboards 解析):
--     * admin 始终全可见(展示范围语义; 图表数据仍由 permission_enforcer 按
--       查看者实时治理);
--     * 其余角色 fail-closed: 未配置任何看板授权 = 一律不可见;
--     * owner / is_public 不再授予"可看"(仅保留编辑权语义)。
--
-- 无种子:
--   admin 在代码层全可见, 无需预置授权行; 非 admin 角色需在角色管理页
--   「看板」页签或看板管理页「可见角色」显式配置后才可见。
--
-- 幂等性(可重复执行):
--   CREATE TABLE IF NOT EXISTS + UNIQUE KEY uk_role_dashboard, 重复执行无副作用。
-- ============================================================================

USE adh;

CREATE TABLE IF NOT EXISTS adh_role_dashboard_access (
  id INT AUTO_INCREMENT PRIMARY KEY,
  role_id INT NOT NULL COMMENT '角色 ID (adh_roles.id)',
  dashboard_id INT NOT NULL COMMENT '仪表盘 ID (adh_dashboards.id)',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_dashboard (role_id, dashboard_id),
  INDEX idx_role (role_id),
  INDEX idx_dashboard (dashboard_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色仪表盘可见性授权';

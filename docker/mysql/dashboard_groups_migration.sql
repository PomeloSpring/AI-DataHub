-- ============================================================================
-- 仪表盘组(看板组合)迁移 (幂等, 可重复执行)
--
-- 背景: 看板目录/轮播的切换维度从"工作空间"改为"仪表盘组":
--   * 组 = 一组看板的编排(组合), 由管理端在可视化配置中维护;
--   * 组按角色分配(勾选绑角色), 角色用户看到该组;
--   * 展示集 = 组内看板 ∩ 角色可见看板(可见性仍由 adh_role_dashboard_access
--     fail-closed 裁决, 组只做编排不授予可见);
--   * 可见但未入组的看板归"未分组"兜底组。
--
-- 幂等性: CREATE TABLE IF NOT EXISTS + UNIQUE KEY, 重复执行无副作用。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_dashboard_groups (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(128) NOT NULL COMMENT '组名称',
  description VARCHAR(512) DEFAULT '' COMMENT '组说明',
  sort INT DEFAULT 0 COMMENT '展示顺序',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_sort (sort)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仪表盘组(看板组合)';

CREATE TABLE IF NOT EXISTS adh_dashboard_group_items (
  id INT AUTO_INCREMENT PRIMARY KEY,
  group_id INT NOT NULL COMMENT '仪表盘组 ID (adh_dashboard_groups.id)',
  dashboard_id BIGINT NOT NULL COMMENT '看板 ID (adh_dashboards.id)',
  sort INT DEFAULT 0 COMMENT '组内顺序',
  UNIQUE KEY uk_group_dashboard (group_id, dashboard_id),
  INDEX idx_group (group_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仪表盘组成员看板';

CREATE TABLE IF NOT EXISTS adh_role_dashboard_groups (
  id INT AUTO_INCREMENT PRIMARY KEY,
  role_id INT NOT NULL COMMENT '角色 ID (adh_roles.id)',
  group_id INT NOT NULL COMMENT '仪表盘组 ID (adh_dashboard_groups.id)',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_group (role_id, group_id),
  INDEX idx_group (group_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色可见的仪表盘组(编排, 不授予看板可见性)';

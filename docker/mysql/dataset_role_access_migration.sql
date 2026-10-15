-- ============================================================================
-- 数据集可见性按角色授权迁移（幂等, 可重复执行）
--
-- 背景:
--   数据集访问范围由用户角色授权 (adh_role_dataset_access) 裁决:
--     * admin 始终全可见(代码层 bypass);
--     * 其余角色 fail-closed: 未配置任何数据集授权 = 一律不可见。
--
-- 幂等性: CREATE TABLE IF NOT EXISTS + UNIQUE KEY, 重复执行无副作用。
-- ============================================================================

USE adh;

CREATE TABLE IF NOT EXISTS adh_role_dataset_access (
  id INT AUTO_INCREMENT PRIMARY KEY,
  role_id BIGINT NOT NULL COMMENT '角色 ID (adh_roles.id)',
  dataset_id BIGINT NOT NULL COMMENT '数据集 ID (adh_datasets.id)',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_dataset (role_id, dataset_id),
  INDEX idx_role (role_id),
  INDEX idx_dataset (dataset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色数据集可见性授权';

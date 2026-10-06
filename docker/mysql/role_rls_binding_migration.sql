-- ============================================================================
-- 角色-数据安全策略绑定迁移 (幂等, 可重复执行)
--
-- 背景: 表/列/属性不再在角色权限弹窗手工配置, 数据安全约束统一在「数据安全
-- 策略」(adh_rls_policies + adh_rls_column_policies + adh_rls_user_attributes)
-- 维护, 角色侧仅通过勾选策略绑定(adh_role_rls_policies)。
--
-- 生效语义(勾选才生效):
--   rls_service.get_effective_policies 仅应用「用户任一角色绑定的策略」;
--   角色未绑定任何策略 = 不施加任何 RLS 行/列限制(与"不配置=不限制"一致)。
--
-- 幂等性(可重复执行):
--   CREATE TABLE IF NOT EXISTS + UNIQUE KEY uk_role_policy, 重复执行无副作用。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_role_rls_policies (
  id INT AUTO_INCREMENT PRIMARY KEY,
  role_id INT NOT NULL COMMENT '角色 ID (adh_roles.id)',
  policy_id BIGINT NOT NULL COMMENT 'RLS 策略 ID (adh_rls_policies.id)',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_policy (role_id, policy_id),
  INDEX idx_policy (policy_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色数据安全策略绑定(勾选才生效)';

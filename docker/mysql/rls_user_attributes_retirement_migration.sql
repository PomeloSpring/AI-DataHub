-- ═══════════════════════════════════════════════════════════════
-- 用户属性机制退役 — RLS 属性值统一由用户角色权限(角色属性 adh_role_attributes)供给
-- ═══════════════════════════════════════════════════════════════
--
-- 背景: 权限/属性配置已全部由用户角色权限管理, 「数据安全策略-用户属性」
-- (按 用户+工作空间 单独配置)整套退役:
--   - 前端用户属性页签、/admin/rls-user-attributes/* 与 /rls/users/*/attributes 端点已移除;
--   - RLS 过滤表达式 :user_xxx 的取值来源收敛为角色属性(role_service.get_user_effective_attributes);
--   - 行/列策略的 filter_type='user_attribute' 引用机制保留(仅值来源变化)。
--
-- 执行: mysql -u root -p adh < rls_user_attributes_retirement_migration.sql
-- 数据: DROP 前请备份(线上已备份至 data/rls_user_attributes_backup.json)

USE adh;

DROP TABLE IF EXISTS adh_rls_user_attributes;

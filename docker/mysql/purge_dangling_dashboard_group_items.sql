-- ============================================================================
-- 清理「看板组合」里指向已删除看板的悬空成员行 (幂等, 可重复执行)
--
-- 背景: adh_dashboard_group_items 对 adh_dashboards 无外键级联, 历史删板只在
--   API 层补删了 charts / role_dashboard_access, 未清理组合成员, 于是管理端
--   「看板组合」穿梭框出现 "看板已删除" 悬空项(保存还会把悬空引用重新写回)。
--   代码层已收口: 删板端点级联删组内引用 + _group_payload 读口按存在性过滤 +
--   set_group_items 写口拒登记不存在 id。本迁移把存量的悬空行一次性清掉。
--
-- 幂等性: 只删 dashboard_id 在 adh_dashboards 中已不存在的行, 重复执行无副作用。
-- ============================================================================

DELETE gi FROM adh_dashboard_group_items gi
LEFT JOIN adh_dashboards d ON d.id = gi.dashboard_id
WHERE d.id IS NULL;

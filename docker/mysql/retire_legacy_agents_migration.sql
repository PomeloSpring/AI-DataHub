-- ============================================================================
-- retire_legacy_agents_migration.sql
-- 退役旧自研 multi-agent 的配置表。执行层唯一后端为 qodercli（qodercn_agent_sdk），
-- secure_sdk/scheduled_analysis 构建 SDK options 时显式 agents={}（不注册任何子 agent），
-- 智能体配置统一由 adh_as_bots（AS-BOT，随角色继承）承载。adh_agents / adh_workspace_agents
-- 已无任何读/写路径消费（能配不生效），此处从存量库彻底清理。
--
-- ⚠️ 仅清理旧 multi-agent 的两张表；**不得**触碰 adh_agent_sessions（当前 AS-BOT 会话表）
--    与 adh_as_bots（AS-BOT 配置表）。
-- 幂等：DROP ... IF EXISTS，可安全重复执行。
-- ============================================================================

DROP TABLE IF EXISTS adh_workspace_agents;
DROP TABLE IF EXISTS adh_agents;

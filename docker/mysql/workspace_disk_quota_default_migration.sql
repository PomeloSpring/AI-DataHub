-- ══════════════════════════════════════════════════════════════════════════
-- 让"每工作空间磁盘配额"真的生效：列缺省值与代码缺省对齐（2026-11 存储治理 P0-2）
-- ══════════════════════════════════════════════════════════════════════════
-- 背景（双口径缺陷）：`adh_user_workspace_quota` 无登记行时，
--   · 建空间门与管理员页按硬编码 5GB 显示/裁决；
--   · 新建会话的磁盘门却把空读成 0 = 不限制。
-- 结果：管理员页写着"每空间 5GB"，实际任何用量都不拦 —— 属"配了没用"。
--
-- 现在的口径（唯一裁决点 backend/core/role_service.get_user_workspace_quota）：
--   无登记行 = 取系统缺省（配置项 WORKSPACE_DISK_QUOTA_DEFAULT_BYTES，默认 500MB）；
--   有登记行 = 行值权威（行里 0 = 管理员显式"不设限"逃生口，仅可经 SQL 配置）。
--
-- 为什么这里**不 INSERT 默认行**：批量插行会把"系统缺省"伪装成"管理员显式配置"，
-- 之后改缺省就再也改不动这批行；且存量行属管理员，绝不能用默认值覆盖
-- （幂等 upsert 覆盖别的批次拥有的列是历史事故口径）。所以只对齐列缺省，
-- 让"将来不带该列插入的行"与代码缺省同值，避免又长出一套数字。
--
-- 幂等：ALTER COLUMN SET DEFAULT 可重复执行，不动任何数据行。
ALTER TABLE adh_user_workspace_quota
  ALTER COLUMN disk_quota_bytes SET DEFAULT 524288000;

-- 核验（应用后手工执行，应看到 524288000）：
-- SELECT COLUMN_DEFAULT FROM information_schema.COLUMNS
--  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_user_workspace_quota'
--    AND COLUMN_NAME = 'disk_quota_bytes';
-- 生效配额（无行用户应显示 524288000）走接口 GET /api/workspaces/quotas 的 disk_quota_bytes。

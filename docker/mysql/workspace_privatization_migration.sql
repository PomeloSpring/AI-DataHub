-- ============================================================================
-- 工作空间用户私有化迁移 (幂等, 可重复执行)
--
-- 背景: 工作空间改为"随用户走"的个人工作站:
--   * 每个用户自动拥有默认工作空间, 可自建更多(受配额限制);
--   * 无成员协作(权限已全部由用户角色裁决), adh_workspace_users 等
--     成员/绑定表冻结不再消费(保留不删);
--   * 管理员统管所有用户的工作空间: 限制每用户空间数 + 每空间磁盘配额。
--
-- 幂等性: CREATE TABLE IF NOT EXISTS + INSERT IGNORE 种子, 重复执行无副作用。
-- ============================================================================

-- 1. 每用户工作空间配额(缺省: 5 个空间 / 每空间 5GB)
CREATE TABLE IF NOT EXISTS adh_user_workspace_quota (
  user_id BIGINT NOT NULL COMMENT '用户 ID (adh_users.id)',
  max_workspaces INT NOT NULL DEFAULT 5 COMMENT '可创建工作空间数上限(含默认空间)',
  disk_quota_bytes BIGINT NOT NULL DEFAULT 5368709120 COMMENT '每工作空间磁盘配额(字节, 默认 5GB)',
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户工作空间配额(空间数+磁盘)';

-- 2. 冻结标记(表保留, 语义退役): 工作空间不再承载成员/资源绑定
--    adh_workspace_users      — 成员体系(随用户走, 无协作)
--    adh_workspace_roles      — 工作空间角色授权(权限已由用户角色裁决)
--    adh_workspace_datasources — 空间绑数据源(已由角色授权裁决)
--    adh_workspace_wakers     — 空间绑 Waker(已由角色一对一裁决)
-- 以上表不再写入/消费, 保留数据以便回溯。

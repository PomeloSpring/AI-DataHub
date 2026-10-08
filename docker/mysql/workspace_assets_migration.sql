-- ============================================================================
-- 工作空间资产清单迁移 (幂等, 可重复执行)
-- 【已退役】资产归属已改为用户级 adh_user_assets(资产跟随用户, 跨工作空间),
-- 本表仅作为迁移来源/冻结备份, 数据已由 user_assets_migration.sql 拷入,
-- 核对无误后可手工 DROP; 新代码不得读写本表。
--
-- 背景: 每个工作空间是一个工作站。会话产物经用户"归档收藏"后进入工作空间级
-- 资产清单(共享项目), 供该空间所有会话引用复用:
--   * 资产本体托管在对象存储(OSS/MinIO, 未配置回退本地), 对象 key 为真值;
--   * 归档后会话本地产物可基于磁盘配额清理(资产不受影响);
--   * LLM 经 assets 工具组感知资产清单并与用户确认归档/清理。
--
-- 幂等性: CREATE TABLE IF NOT EXISTS, 重复执行无副作用。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_workspace_assets (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY COMMENT '资产 ID (uuid hex)',
  workspace_id BIGINT NOT NULL COMMENT '所属工作空间 (adh_workspaces.id)',
  name VARCHAR(255) NOT NULL COMMENT '资产名称(展示用)',
  filename VARCHAR(255) NOT NULL COMMENT '原始文件名',
  category VARCHAR(32) DEFAULT 'file' COMMENT '类别: file/image/report/sql/other',
  object_key VARCHAR(512) NOT NULL COMMENT '对象存储 key (真值源)',
  storage_type VARCHAR(16) DEFAULT 'object' COMMENT 'object=对象存储 / local=本地回退',
  size BIGINT DEFAULT 0 COMMENT '字节数',
  source_conversation_id BIGINT DEFAULT 0 COMMENT '来源会话 ID (0=手动上传)',
  created_by BIGINT COMMENT '归档人 (adh_users.id)',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_workspace (workspace_id),
  INDEX idx_ws_name (workspace_id, name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工作空间资产清单(OSS 托管, 跨会话共享)';

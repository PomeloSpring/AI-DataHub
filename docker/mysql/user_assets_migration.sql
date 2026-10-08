-- ============================================================================
-- 用户资产清单迁移 (幂等, information_schema 守卫, 可重复执行)
--
-- 背景: 资产清单跟随**用户**而非工作空间 —— 会话产物经"归档收藏"进入个人资产
-- 清单, 一旦归档跟着用户走(跨工作空间):
--   * 资产本体托管对象存储(OSS/MinIO, 未配置回退本地), 对象 key 为真值
--     ({prefix}/{user_id}/assets/{asset_id}_{filename}, 见 object_storage.build_asset_key);
--   * origin_workspace_id/source_conversation_id 仅作溯源展示与本地产物清理定位;
--   * 归档后会话本地产物可基于磁盘配额清理(资产不受影响);
--   * LLM 经 assets 工具组感知资产清单并与用户确认归档/清理。
--
-- 旧表 adh_workspace_assets(工作空间级资产)数据迁入 adh_user_assets:
--   * user_id 回填 = 归档人 created_by(缺失时取工作空间属主 adh_workspaces.owner_id);
--   * origin_workspace_id = 原 workspace_id;
--   * source_path 从旧 object_key 尾段还原(首个 '_' 后为归档文件名)。
--   * 对象搬迁(旧 key workspaces/{ws}/assets/... -> 新 key)见
--     scripts/migrate_user_assets_objects.py(幂等可重跑); 旧表退役冻结,
--     迁移核对无误后可手工 DROP。
--
-- 幂等性: CREATE TABLE IF NOT EXISTS + 按主键去重拷贝, 重复执行无副作用。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_user_assets (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY COMMENT '资产 ID (uuid hex)',
  user_id BIGINT NOT NULL COMMENT '资产属主 (adh_users.id) — 资产跟随用户, 跨工作空间',
  origin_workspace_id BIGINT NOT NULL DEFAULT 0 COMMENT '来源工作空间 (adh_workspaces.id, 仅溯源/清理定位; 空间已删=0)',
  name VARCHAR(255) NOT NULL COMMENT '资产名称(展示用)',
  filename VARCHAR(255) NOT NULL COMMENT '原始文件名',
  category VARCHAR(32) DEFAULT 'file' COMMENT '类别: file/image/report/sql/other',
  object_key VARCHAR(512) NOT NULL COMMENT '对象存储 key (真值源, {prefix}/{user_id}/assets/{id}_{filename})',
  storage_type VARCHAR(16) DEFAULT 'object' COMMENT 'object=对象存储 / local=本地回退',
  size BIGINT DEFAULT 0 COMMENT '字节数',
  source_conversation_id BIGINT DEFAULT 0 COMMENT '来源会话 ID (0=手动上传)',
  source_path VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '来源文件在会话工作区的相对路径(清理定位用)',
  created_by BIGINT COMMENT '归档人 (adh_users.id)',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_user (user_id),
  INDEX idx_user_name (user_id, name),
  INDEX idx_origin_ws (origin_workspace_id, source_conversation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户资产清单(OSS 托管, 跟随用户跨工作空间)';

-- 早期版本无 source_path 列时补齐(幂等: information_schema 守卫)
SET @has_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE table_schema = DATABASE() AND table_name = 'adh_user_assets'
                   AND column_name = 'source_path');
SET @ddl := IF(@has_col = 0,
  'ALTER TABLE adh_user_assets ADD COLUMN source_path VARCHAR(1024) NOT NULL DEFAULT '''' COMMENT ''来源文件在会话工作区的相对路径(清理定位用)'' AFTER source_conversation_id',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 旧数据迁移(幂等: 已迁入的行按主键跳过; 旧表不存在时跳过拷贝)
SET @has_old := (SELECT COUNT(*) FROM information_schema.TABLES
                  WHERE table_schema = DATABASE() AND table_name = 'adh_workspace_assets');
SET @copy := IF(@has_old > 0,
  'INSERT INTO adh_user_assets '
  '(id, user_id, origin_workspace_id, name, filename, category, object_key, storage_type, size, '
  ' source_conversation_id, source_path, created_by, created_at) '
  'SELECT a.id, COALESCE(NULLIF(a.created_by, 0), NULLIF(w.owner_id, 0), 0), a.workspace_id, '
  ' a.name, a.filename, a.category, a.object_key, a.storage_type, a.size, a.source_conversation_id, '
  ' COALESCE(NULLIF(MID(a.object_key, LOCATE(''_'', a.object_key) + 1), ''''), a.filename), '
  ' a.created_by, a.created_at '
  'FROM adh_workspace_assets a LEFT JOIN adh_workspaces w ON w.id = a.workspace_id '
  'WHERE NOT EXISTS (SELECT 1 FROM adh_user_assets u WHERE u.id = a.id)',
  'SELECT 1');
PREPARE stmt2 FROM @copy; EXECUTE stmt2; DEALLOCATE PREPARE stmt2;

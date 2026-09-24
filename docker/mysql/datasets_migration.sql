-- Datasets 数据集模块迁移（幂等: CREATE IF NOT EXISTS + INSERT IGNORE + 按唯一键去重）
-- 1. adh_datasets        BI 数据集(语义对象/SQL 双来源, 治理建模层)
-- 2. adh_dataset_scopes  数据集级行范围(在 RLS/敏感基线之上追加, 只增不减)
-- 3. 权限码/菜单种子 + 角色默认绑定
-- 4. 旧 adh_saved_queries.is_dataset=1 → SQL 数据集(平滑迁移)

USE adh;

CREATE TABLE IF NOT EXISTS adh_datasets (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(128) NOT NULL UNIQUE COMMENT '数据集名(全局唯一)',
  description VARCHAR(512) DEFAULT '',
  source_type VARCHAR(16) NOT NULL DEFAULT 'semantic' COMMENT 'semantic|sql',
  object_key VARCHAR(128) DEFAULT '' COMMENT 'semantic 源: 本体对象 key',
  datasource_id BIGINT DEFAULT 0 COMMENT 'semantic:对象所属数据源; sql:执行数据源(0=默认引擎)',
  sql_query TEXT COMMENT 'sql 源: 仅允许 SELECT/WITH, 经 validate_sql 校验',
  field_config JSON COMMENT 'sql 源字段元数据 [{field,role:dim|measure,label,fmt}]',
  visibility VARCHAR(16) DEFAULT 'workspace' COMMENT 'private|workspace|public',
  owner_id BIGINT DEFAULT 0,
  status VARCHAR(16) DEFAULT 'active' COMMENT 'active|inactive',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_ds_source (source_type),
  INDEX idx_ds_owner (owner_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='BI数据集(治理建模层)';

CREATE TABLE IF NOT EXISTS adh_dataset_scopes (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  dataset_id BIGINT NOT NULL,
  subject_type VARCHAR(8) NOT NULL DEFAULT 'user' COMMENT 'user|role',
  subject_id BIGINT NOT NULL COMMENT 'adh_users.id 或 adh_roles.id',
  filters JSON NOT NULL COMMENT '[{field,op,value}] 与查询条件 AND 合并, 只收紧不放宽',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_scope_ds (dataset_id),
  INDEX idx_scope_subject (subject_type, subject_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据集行级范围(分享时生效)';

-- ============================================================================
-- 权限码种子(权限码注册表驱动菜单可见性与 API 鉴权)
-- ============================================================================
INSERT IGNORE INTO adh_perm_registry (perm_code, label, module, module_label, api_pattern, api_method, menu_key, sort) VALUES
('dataset:read',   '查看数据集', 'data', '数据中台', '/api/datasets*', 'GET',  'data:datasets', 141),
('dataset:query',  '数据集取数', 'data', '数据中台', '/api/datasets/*/preview,/api/datasets/*/query,/api/datasets/validate-sql', 'POST', 'data:datasets', 143),
('dataset:manage', '管理数据集', 'data', '数据中台', '/api/datasets*', 'POST,PUT,DELETE', 'data:datasets', 142);

INSERT IGNORE INTO adh_menu_registry (menu_key, label, section, module, sort) VALUES
('data:datasets', '数据集', '语义建模', 'data', 12);

-- 回填菜单权限码关联
UPDATE adh_menu_registry mr
  JOIN adh_perm_registry p ON p.menu_key = mr.menu_key
  SET mr.perm_code = p.perm_code
  WHERE p.menu_key <> '' AND (mr.perm_code IS NULL OR mr.perm_code = '');

-- 角色默认: analyst(2)/full_analyst(300) 读写; viewer(3)/data_viewer(200)/region_analyst(100) 只读+取数
INSERT IGNORE INTO adh_role_perms (role_id, perm_code) VALUES
(2,'dataset:read'),(2,'dataset:query'),(2,'dataset:manage'),
(300,'dataset:read'),(300,'dataset:query'),(300,'dataset:manage'),
(3,'dataset:read'),(3,'dataset:query'),
(200,'dataset:read'),(200,'dataset:query'),
(100,'dataset:read'),(100,'dataset:query');

-- ============================================================================
-- 旧 is_dataset 标记的 saved query → SQL 数据集(name 唯一键去重, 重跑不重复)
-- ============================================================================
INSERT IGNORE INTO adh_datasets (name, description, source_type, sql_query, visibility, owner_id)
SELECT sq.name, COALESCE(sq.description, ''), 'sql', sq.sql_query, 'private', sq.owner_id
FROM adh_saved_queries sq
WHERE sq.is_dataset = 1;

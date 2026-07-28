-- ═══════════════════════════════════════════════════════════════
-- 角色数据权限表 — MySQL 版
-- 执行: mysql -u root -p < role_permission_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh;

-- 角色-数据源权限
CREATE TABLE IF NOT EXISTS adh_role_datasource_access (
    id              BIGINT PRIMARY KEY,
    role_id         BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL,
    access_type     VARCHAR(32) NOT NULL DEFAULT 'read',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_role_ds (role_id, datasource_id),
    INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色数据源权限';

-- 角色-表权限
CREATE TABLE IF NOT EXISTS adh_role_table_access (
    id              BIGINT PRIMARY KEY,
    role_id         BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    table_name      VARCHAR(128) NOT NULL,
    access_type     VARCHAR(32) NOT NULL DEFAULT 'read',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_role_ds_table (role_id, datasource_id, table_name),
    INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色表权限';

-- 角色-列权限
CREATE TABLE IF NOT EXISTS adh_role_column_access (
    id              BIGINT PRIMARY KEY,
    role_id         BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    table_name      VARCHAR(128) NOT NULL,
    column_name     VARCHAR(128) NOT NULL,
    access_type     VARCHAR(32) NOT NULL DEFAULT 'visible',
    mask_pattern    VARCHAR(64) DEFAULT '',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_role_ds_table_col (role_id, datasource_id, table_name, column_name),
    INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色列权限';

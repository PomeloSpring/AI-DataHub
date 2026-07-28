-- ═══════════════════════════════════════════════════════════════
-- 角色权限体系 — MySQL 版
-- 执行: mysql -u root -p < role_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh;

-- 角色定义表
CREATE TABLE IF NOT EXISTS adh_roles (
    id              BIGINT PRIMARY KEY,
    name            VARCHAR(64) NOT NULL UNIQUE COMMENT '角色标识',
    display_name    VARCHAR(128) NOT NULL COMMENT '显示名称',
    description     TEXT COMMENT '描述',
    is_system       TINYINT DEFAULT 0 COMMENT '系统内置角色不可删除',
    is_active       TINYINT DEFAULT 1,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色定义';

-- 角色-数据范围属性表
CREATE TABLE IF NOT EXISTS adh_role_attributes (
    id              BIGINT PRIMARY KEY,
    role_id         BIGINT NOT NULL COMMENT '角色 ID',
    workspace_id    BIGINT NOT NULL DEFAULT 0 COMMENT '工作空间 ID',
    attr_key        VARCHAR(64) NOT NULL COMMENT '属性名',
    attr_value      VARCHAR(256) NOT NULL COMMENT '属性值',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_role (role_id),
    INDEX idx_workspace (workspace_id),
    UNIQUE KEY uk_role_ws_key (role_id, workspace_id, attr_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色数据范围属性';

-- 工作空间-角色关联表
CREATE TABLE IF NOT EXISTS adh_workspace_roles (
    id              BIGINT PRIMARY KEY,
    workspace_id    BIGINT NOT NULL COMMENT '工作空间 ID',
    role_id         BIGINT NOT NULL COMMENT '角色 ID',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_ws_role (workspace_id, role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工作空间角色授权';

-- 用户-角色关联表
CREATE TABLE IF NOT EXISTS adh_user_roles (
    id              BIGINT PRIMARY KEY,
    user_id         BIGINT NOT NULL COMMENT '用户 ID',
    role_id         BIGINT NOT NULL COMMENT '角色 ID',
    workspace_id    BIGINT NOT NULL DEFAULT 0 COMMENT '0=全局角色',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_role (role_id),
    UNIQUE KEY uk_user_role_ws (user_id, role_id, workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户角色分配';

-- 插入系统内置角色
INSERT IGNORE INTO adh_roles (id, name, display_name, description, is_system) VALUES
    (1, 'admin', '管理员', '系统管理员，拥有所有权限', 1),
    (2, 'analyst', '数据分析师', '可查看所有数据，无管理权限', 1),
    (3, 'viewer', '普通用户', '只能查看被授权的数据', 1);

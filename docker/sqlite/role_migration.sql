-- ═══════════════════════════════════════════════════════════════
-- 角色权限体系 — SQLite 版
-- ═══════════════════════════════════════════════════════════════

PRAGMA journal_mode=WAL;

-- 角色定义表
CREATE TABLE IF NOT EXISTS adh_roles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    display_name    TEXT NOT NULL,
    description     TEXT DEFAULT '',
    is_system       INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 角色-数据范围属性表（替代原来的 adh_rls_user_attributes）
CREATE TABLE IF NOT EXISTS adh_role_attributes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id         INTEGER NOT NULL,
    workspace_id    INTEGER NOT NULL DEFAULT 0,
    attr_key        TEXT NOT NULL,
    attr_value      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ra_role ON adh_role_attributes(role_id);
CREATE INDEX IF NOT EXISTS idx_ra_ws ON adh_role_attributes(workspace_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_ra ON adh_role_attributes(role_id, workspace_id, attr_key);

-- 工作空间-角色关联表（替代原来的逐用户授权）
CREATE TABLE IF NOT EXISTS adh_workspace_roles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    INTEGER NOT NULL,
    role_id         INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_wr ON adh_workspace_roles(workspace_id, role_id);

-- 用户-角色关联表（用户可以拥有多个角色）
CREATE TABLE IF NOT EXISTS adh_user_roles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    role_id         INTEGER NOT NULL,
    workspace_id    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ur_user ON adh_user_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_ur_role ON adh_user_roles(role_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_ur ON adh_user_roles(user_id, role_id, workspace_id);

-- 插入系统内置角色
INSERT OR IGNORE INTO adh_roles (id, name, display_name, description, is_system) VALUES
    (1, 'admin', '管理员', '系统管理员，拥有所有权限', 1),
    (2, 'analyst', '数据分析师', '可查看所有数据，无管理权限', 1),
    (3, 'viewer', '普通用户', '只能查看被授权的数据', 1);

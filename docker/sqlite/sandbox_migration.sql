-- AI-DataHub SQLite 沙箱环境管理表
-- 运行: sqlite3 data/metadata.db < docker/sqlite/sandbox_migration.sql

PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS adh_sandbox_environments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    sandbox_type    TEXT NOT NULL,
    display_name    TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    config          TEXT NOT NULL DEFAULT '{}',
    resource_info   TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'unknown',
    is_default      INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    last_heartbeat  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sandbox_type ON adh_sandbox_environments(sandbox_type);
CREATE INDEX IF NOT EXISTS idx_sandbox_active ON adh_sandbox_environments(is_active);

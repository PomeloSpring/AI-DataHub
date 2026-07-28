-- 沙箱执行日志表
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS adh_sandbox_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sandbox_id INTEGER NOT NULL,
    sandbox_name TEXT DEFAULT '',
    sandbox_type TEXT DEFAULT '',
    code TEXT NOT NULL,
    requirements TEXT DEFAULT '[]',
    success INTEGER DEFAULT 0,
    stdout TEXT DEFAULT '',
    stderr TEXT DEFAULT '',
    result TEXT DEFAULT '',
    error TEXT DEFAULT '',
    elapsed_ms INTEGER DEFAULT 0,
    conversation_id INTEGER DEFAULT 0,
    user_id INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sandbox_log_sandbox ON adh_sandbox_logs(sandbox_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_log_user ON adh_sandbox_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_log_created ON adh_sandbox_logs(created_at);

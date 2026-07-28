-- AI-DataHub SQLite 功能扩展表
-- 包含: RLS行级安全 + 知识管理 + 质量评审 + 数据建模
-- 运行: sqlite3 data/metadata.db < docker/sqlite/feature_migrations.sql

PRAGMA journal_mode=WAL;

-- ═══════════════════════════════════════════════════════════════════
-- 1. RLS 行级安全
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS adh_rls_policies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    workspace_id    INTEGER NOT NULL DEFAULT 0,
    datasource_id   INTEGER NOT NULL DEFAULT 0,
    table_name      TEXT NOT NULL,
    policy_type     TEXT NOT NULL DEFAULT 'both',
    filter_type     TEXT NOT NULL DEFAULT 'condition',
    filter_expr     TEXT DEFAULT '',
    user_attribute  TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 1,
    created_by      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rls_ws_table ON adh_rls_policies(workspace_id, table_name);
CREATE INDEX IF NOT EXISTS idx_rls_ds ON adh_rls_policies(datasource_id);
CREATE INDEX IF NOT EXISTS idx_rls_active ON adh_rls_policies(is_active);

CREATE TABLE IF NOT EXISTS adh_rls_column_policies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id       INTEGER NOT NULL,
    column_name     TEXT NOT NULL,
    access_type     TEXT NOT NULL DEFAULT 'visible',
    mask_pattern    TEXT DEFAULT '',
    description     TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_rls_col_policy ON adh_rls_column_policies(policy_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_rls_col ON adh_rls_column_policies(policy_id, column_name);

CREATE TABLE IF NOT EXISTS adh_rls_user_attributes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    workspace_id    INTEGER NOT NULL DEFAULT 0,
    attr_key        TEXT NOT NULL,
    attr_value      TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_rls_ua ON adh_rls_user_attributes(user_id, workspace_id, attr_key);
CREATE INDEX IF NOT EXISTS idx_rls_ua_ws ON adh_rls_user_attributes(workspace_id);

CREATE TABLE IF NOT EXISTS adh_rls_audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    workspace_id    INTEGER NOT NULL DEFAULT 0,
    policy_id       INTEGER,
    policy_name     TEXT DEFAULT '',
    table_name      TEXT DEFAULT '',
    action          TEXT NOT NULL,
    original_sql    TEXT DEFAULT '',
    filtered_sql    TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rls_audit_user ON adh_rls_audit_logs(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_rls_audit_ws ON adh_rls_audit_logs(workspace_id);

-- ═══════════════════════════════════════════════════════════════════
-- 2. 知识管理
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS adh_knowledge_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    INTEGER NOT NULL DEFAULT 0,
    datasource_id   INTEGER DEFAULT 0,
    knowledge_type  TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    metadata        TEXT DEFAULT '',
    related_tables  TEXT DEFAULT '',
    priority        INTEGER DEFAULT 0,
    usage_count     INTEGER DEFAULT 0,
    positive_count  INTEGER DEFAULT 0,
    negative_count  INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    embedding       TEXT DEFAULT '',
    created_by      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ki_ws_type ON adh_knowledge_items(workspace_id, knowledge_type);
CREATE INDEX IF NOT EXISTS idx_ki_ds_type ON adh_knowledge_items(datasource_id, knowledge_type);
CREATE INDEX IF NOT EXISTS idx_ki_active ON adh_knowledge_items(is_active);

CREATE TABLE IF NOT EXISTS adh_followup_cases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_id    INTEGER NOT NULL,
    followup_order  INTEGER NOT NULL DEFAULT 0,
    followup_question TEXT NOT NULL,
    expected_sql    TEXT DEFAULT '',
    expected_result TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fc_ki ON adh_followup_cases(knowledge_id);

-- ═══════════════════════════════════════════════════════════════════
-- 3. 质量评审
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS adh_quality_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    INTEGER NOT NULL DEFAULT 0,
    conversation_id INTEGER DEFAULT 0,
    message_id      INTEGER DEFAULT 0,
    user_id         INTEGER NOT NULL,
    username        TEXT DEFAULT '',
    question        TEXT NOT NULL,
    generated_sql   TEXT DEFAULT '',
    execution_result TEXT DEFAULT '',
    datasource_id   INTEGER DEFAULT 0,
    score_understanding   INTEGER,
    score_correctness     INTEGER,
    score_completeness    INTEGER,
    score_helpfulness     INTEGER,
    score_overall         INTEGER,
    auto_review     TEXT DEFAULT '',
    auto_reviewed_at TEXT,
    manual_review   TEXT DEFAULT '',
    manual_score    INTEGER,
    reviewed_by     INTEGER,
    reviewed_at     TEXT,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_qr_ws_st ON adh_quality_reviews(workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_qr_user ON adh_quality_reviews(user_id);
CREATE INDEX IF NOT EXISTS idx_qr_score ON adh_quality_reviews(score_overall);
CREATE INDEX IF NOT EXISTS idx_qr_created ON adh_quality_reviews(created_at);

CREATE TABLE IF NOT EXISTS adh_quality_tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id       INTEGER NOT NULL,
    tag_type        TEXT DEFAULT '',
    tag_value       TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_qt_review ON adh_quality_tags(review_id);
CREATE INDEX IF NOT EXISTS idx_qt_tag ON adh_quality_tags(tag_type, tag_value);

CREATE TABLE IF NOT EXISTS adh_quality_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    INTEGER NOT NULL DEFAULT 0,
    stat_date       TEXT NOT NULL,
    total_queries   INTEGER DEFAULT 0,
    avg_score       REAL,
    score_distribution TEXT DEFAULT '',
    issue_top_tags  TEXT DEFAULT '',
    auto_review_rate REAL,
    manual_review_rate REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_qs_ws_date ON adh_quality_stats(workspace_id, stat_date);

-- ═══════════════════════════════════════════════════════════════════
-- 4. 数据建模
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS adh_data_models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    INTEGER NOT NULL DEFAULT 0,
    datasource_id   INTEGER NOT NULL,
    model_name      TEXT NOT NULL,
    description     TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 1,
    created_by      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dm_ws_ds ON adh_data_models(workspace_id, datasource_id);

CREATE TABLE IF NOT EXISTS adh_model_tables (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL,
    table_name      TEXT NOT NULL,
    display_name    TEXT DEFAULT '',
    business_desc   TEXT DEFAULT '',
    is_visible      INTEGER DEFAULT 1,
    position_x      REAL DEFAULT 0,
    position_y      REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mt_model ON adh_model_tables(model_id);
CREATE UNIQUE INDEX IF NOT EXISTS uk_mt ON adh_model_tables(model_id, table_name);

CREATE TABLE IF NOT EXISTS adh_model_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL,
    source_table    TEXT NOT NULL,
    source_column   TEXT NOT NULL,
    target_table    TEXT NOT NULL,
    target_column   TEXT NOT NULL,
    join_type       TEXT DEFAULT 'INNER',
    relation_type   TEXT DEFAULT '1:N',
    description     TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_mr_model ON adh_model_relations(model_id);

CREATE TABLE IF NOT EXISTS adh_calculated_fields (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL,
    table_name      TEXT NOT NULL,
    field_name      TEXT NOT NULL,
    display_name    TEXT DEFAULT '',
    expression      TEXT NOT NULL,
    data_type       TEXT DEFAULT 'number',
    description     TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_cf_model ON adh_calculated_fields(model_id, table_name);

-- ═══════════════════════════════════════════════════════════════════
-- 5. 工作空间扩展（如不存在则创建）
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS adh_workspaces (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    icon            TEXT DEFAULT '📊',
    color           TEXT DEFAULT '#1890ff',
    owner_id        INTEGER NOT NULL,
    is_default      INTEGER DEFAULT 0,
    config          TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS adh_workspace_users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    role            TEXT DEFAULT 'member',
    is_default      INTEGER DEFAULT 0,
    joined_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_ws_user ON adh_workspace_users(workspace_id, user_id);

CREATE TABLE IF NOT EXISTS adh_workspace_datasources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    INTEGER NOT NULL,
    datasource_id   INTEGER NOT NULL,
    is_primary      INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_ws_ds ON adh_workspace_datasources(workspace_id, datasource_id);

-- ═══════════════════════════════════════════════════════════════════
-- 6. 定时任务扩展（如不存在则创建）
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS adh_notification_channels (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    channel_type    TEXT NOT NULL,
    config          TEXT NOT NULL,
    is_active       INTEGER DEFAULT 1,
    workspace_id    INTEGER DEFAULT 0,
    owner_id        INTEGER NOT NULL,
    last_test_at    TEXT,
    last_test_status TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS adh_scheduled_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    task_type       TEXT NOT NULL,
    task_config     TEXT NOT NULL,
    report_template_key TEXT DEFAULT '',
    cron_expression TEXT NOT NULL,
    timezone        TEXT DEFAULT 'Asia/Shanghai',
    channel_id      INTEGER,
    notify_on_success INTEGER DEFAULT 1,
    notify_on_failure INTEGER DEFAULT 1,
    is_active       INTEGER DEFAULT 1,
    workspace_id    INTEGER DEFAULT 0,
    owner_id        INTEGER NOT NULL,
    last_run_at     TEXT,
    last_status     TEXT,
    last_error      TEXT,
    run_count       INTEGER DEFAULT 0,
    timeout_seconds INTEGER DEFAULT 300,
    max_retries     INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS adh_scheduled_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scheduled_task_id INTEGER NOT NULL,
    workspace_id    INTEGER DEFAULT 0,
    status          TEXT NOT NULL,
    trigger_type    TEXT NOT NULL,
    celery_task_id  TEXT DEFAULT '',
    result_summary  TEXT DEFAULT '',
    result_data     TEXT DEFAULT '',
    error_message   TEXT DEFAULT '',
    questions_executed TEXT DEFAULT '',
    questions_succeeded INTEGER DEFAULT 0,
    questions_failed INTEGER DEFAULT 0,
    report_content  TEXT DEFAULT '',
    channel_response TEXT DEFAULT '',
    notify_status   TEXT DEFAULT '',
    elapsed_ms      INTEGER DEFAULT 0,
    token_usage     TEXT DEFAULT '',
    worker_id       TEXT DEFAULT '',
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_st_task ON adh_scheduled_logs(scheduled_task_id);
CREATE INDEX IF NOT EXISTS idx_st_status ON adh_scheduled_logs(status);

-- AI-DataHub SQLite 元数据表
-- 用于轻量级部署和测试环境
-- 运行: sqlite3 data/metadata.db < docker/sqlite/init.sql

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============================================================================
-- 数据源配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_datasources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    db_type         TEXT NOT NULL DEFAULT 'mysql',
    host            TEXT NOT NULL,
    port            INTEGER NOT NULL DEFAULT 3306,
    username        TEXT NOT NULL,
    password        TEXT NOT NULL,
    database_name   TEXT DEFAULT '',
    is_default      INTEGER DEFAULT 0,
    ssl             INTEGER DEFAULT 0,
    owner_id        INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- 表级元数据
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_table_info (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource_id       INTEGER NOT NULL DEFAULT 0,
    table_name          TEXT NOT NULL,
    table_comment       TEXT DEFAULT '',
    table_business_desc TEXT DEFAULT '',
    keywords            TEXT DEFAULT '',
    region_tag          TEXT DEFAULT '',
    domain_tag          TEXT DEFAULT '',
    is_active           INTEGER DEFAULT 1,
    sync_time           TEXT NOT NULL,
    embedding           TEXT
);

CREATE INDEX IF NOT EXISTS idx_table_info_ds ON adh_table_info(datasource_id);
CREATE INDEX IF NOT EXISTS idx_table_info_active ON adh_table_info(is_active);

-- ============================================================================
-- 字段级元数据
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_column_metadata (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource_id   INTEGER NOT NULL DEFAULT 0,
    table_name      TEXT NOT NULL,
    column_name     TEXT NOT NULL,
    data_type       TEXT NOT NULL,
    column_comment  TEXT DEFAULT '',
    business_desc   TEXT DEFAULT '',
    is_key          TEXT DEFAULT 'false',
    is_nullable     TEXT DEFAULT 'YES',
    is_active       INTEGER DEFAULT 1,
    sync_time       TEXT NOT NULL,
    embedding       TEXT
);

CREATE INDEX IF NOT EXISTS idx_column_ds ON adh_column_metadata(datasource_id, table_name);
CREATE INDEX IF NOT EXISTS idx_column_active ON adh_column_metadata(is_active);

-- ============================================================================
-- SQL 模板表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_sql_templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource_id   INTEGER NOT NULL DEFAULT 0,
    template_id     TEXT NOT NULL UNIQUE,
    template_name   TEXT NOT NULL,
    category        TEXT NOT NULL,
    intent_keywords TEXT NOT NULL,
    sql_template    TEXT NOT NULL,
    variables       TEXT,
    rules           TEXT,
    description     TEXT DEFAULT '',
    usage_count     INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    embedding       TEXT
);

CREATE INDEX IF NOT EXISTS idx_template_active ON adh_sql_templates(is_active);

-- ============================================================================
-- 业务术语表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_business_terms (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource_id   INTEGER NOT NULL DEFAULT 0,
    term_cn         TEXT NOT NULL,
    term_en         TEXT DEFAULT '',
    term_aliases    TEXT DEFAULT '',
    term_type       TEXT NOT NULL,
    target_table    TEXT DEFAULT '',
    target_column   TEXT DEFAULT '',
    calculation     TEXT,
    description     TEXT DEFAULT '',
    usage_count     INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    embedding       TEXT
);

CREATE INDEX IF NOT EXISTS idx_term_cn ON adh_business_terms(term_cn);
CREATE INDEX IF NOT EXISTS idx_term_active ON adh_business_terms(is_active);

-- ============================================================================
-- 查询审计表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_query_audit (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource_id     INTEGER NOT NULL DEFAULT 0,
    user_id           TEXT NOT NULL,
    username          TEXT NOT NULL,
    user_role         TEXT NOT NULL,
    question          TEXT NOT NULL,
    generated_sql     TEXT,
    query_type        TEXT DEFAULT 'sql',
    execution_status  TEXT NOT NULL,
    row_count         INTEGER DEFAULT 0,
    execution_time_ms INTEGER DEFAULT 0,
    error_message     TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON adh_query_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON adh_query_audit(created_at);

-- ============================================================================
-- 仪表盘表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_dashboards (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    description       TEXT,
    layout            TEXT,
    filters           TEXT,
    params            TEXT,
    status            TEXT DEFAULT 'designing',
    owner_id          INTEGER NOT NULL,
    is_public         INTEGER DEFAULT 0,
    is_default        INTEGER DEFAULT 0,
    carousel_interval INTEGER DEFAULT 0,
    sort_order        INTEGER DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- 图表表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_charts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dashboard_id  INTEGER NOT NULL,
    name          TEXT NOT NULL,
    chart_type    TEXT NOT NULL,
    sql_query     TEXT,
    config        TEXT,
    position      TEXT,
    source_type   TEXT DEFAULT 'query',
    source_id     INTEGER,
    data_cache    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chart_dashboard ON adh_charts(dashboard_id);

-- ============================================================================
-- 图表快照表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_chart_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    question        TEXT,
    sql_query       TEXT,
    chart_type      TEXT,
    brief           TEXT,
    columns         TEXT,
    data_snapshot   TEXT,
    row_count       INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_snapshot_user ON adh_chart_snapshots(user_id);

-- ============================================================================
-- 对话表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL DEFAULT '新对话',
    user_id         INTEGER NOT NULL,
    embed_user_id   TEXT DEFAULT '',
    datasource_id   INTEGER NOT NULL DEFAULT 0,
    messages        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conv_user ON adh_conversations(user_id);

-- ============================================================================
-- 对话消息表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    sql_query       TEXT,
    warnings        TEXT,
    thinking        TEXT,
    rag             TEXT,
    result          TEXT,
    error           TEXT,
    feedback        TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_msg_conv ON adh_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msg_created ON adh_messages(created_at);

-- ============================================================================
-- 保存的查询 / 数据集表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_saved_queries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    description       TEXT,
    sql_query         TEXT NOT NULL,
    is_dataset        INTEGER DEFAULT 0,
    dataset_keywords  TEXT DEFAULT '',
    owner_id          INTEGER NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- 菜单树
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_menu_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER,
    name        TEXT NOT NULL,
    icon        TEXT DEFAULT '',
    page_id     INTEGER,
    link_type   TEXT DEFAULT 'page',
    is_system   INTEGER DEFAULT 0,
    sort_order  INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- 表关联关系表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_table_relations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource_id     INTEGER NOT NULL DEFAULT 0,
    source_table      TEXT NOT NULL,
    source_column     TEXT NOT NULL,
    target_table      TEXT NOT NULL,
    target_column     TEXT NOT NULL,
    relation_type     TEXT DEFAULT '1:N',
    join_type         TEXT DEFAULT 'INNER',
    description       TEXT DEFAULT '',
    is_active         INTEGER DEFAULT 1,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    embedding         TEXT
);

CREATE INDEX IF NOT EXISTS idx_relation_source ON adh_table_relations(source_table);
CREATE INDEX IF NOT EXISTS idx_relation_target ON adh_table_relations(target_table);
CREATE INDEX IF NOT EXISTS idx_relation_active ON adh_table_relations(is_active);

-- ============================================================================
-- 用户表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    email           TEXT DEFAULT '',
    phone           TEXT DEFAULT '',
    avatar          TEXT DEFAULT '',
    user_role       TEXT NOT NULL DEFAULT 'viewer',
    status          TEXT NOT NULL DEFAULT 'active',
    last_login      TEXT,
    login_attempts  INTEGER DEFAULT 0,
    locked_until    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- 操作审计日志表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    username        TEXT NOT NULL,
    action          TEXT NOT NULL,
    module          TEXT DEFAULT '',
    target_type     TEXT DEFAULT '',
    target_id       INTEGER DEFAULT 0,
    detail          TEXT,
    ip_address      TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_log_user ON adh_audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON adh_audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_module ON adh_audit_logs(module);

-- ============================================================================
-- 第三方应用表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_applications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    app_key_hash        TEXT NOT NULL,
    status              TEXT DEFAULT 'active',
    enable_chat         INTEGER DEFAULT 1,
    allowed_dashboards  TEXT,
    allowed_tables      TEXT,
    rate_limit          INTEGER DEFAULT 60,
    description         TEXT DEFAULT '',
    last_used_at        TEXT,
    created_by          INTEGER NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- 嵌入调用日志表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_embed_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id          INTEGER NOT NULL,
    user_id         TEXT NOT NULL,
    user_name       TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT,
    ip_address      TEXT DEFAULT '',
    status          TEXT DEFAULT 'success',
    error_message   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_embed_app ON adh_embed_logs(app_id);
CREATE INDEX IF NOT EXISTS idx_embed_created ON adh_embed_logs(created_at);

-- ============================================================================
-- 系统配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_system_config (
    config_key      TEXT PRIMARY KEY,
    config_value    TEXT NOT NULL,
    description     TEXT DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 默认配置
INSERT OR IGNORE INTO adh_system_config (config_key, config_value, description)
VALUES ('max_interpretation_rounds', '3', '分析结果解读最大LLM轮数');

INSERT OR IGNORE INTO adh_system_config (config_key, config_value, description)
VALUES ('enable_metadata_supplementation', '1', '启用元数据补充功能（Loop Engineering）');

-- ============================================================================
-- Prompt管理表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_prompts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_key          TEXT NOT NULL UNIQUE,
    prompt_name         TEXT NOT NULL,
    system_prompt       TEXT,
    user_prompt_template TEXT,
    description         TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    created_by          TEXT,
    change_log          TEXT
);

-- ============================================================================
-- Prompt版本历史表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_prompt_versions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id           INTEGER NOT NULL,
    prompt_key          TEXT NOT NULL,
    version             INTEGER NOT NULL,
    system_prompt       TEXT,
    user_prompt_template TEXT,
    change_log          TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    created_by          TEXT,
    is_current          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pv_key ON adh_prompt_versions(prompt_key);
CREATE INDEX IF NOT EXISTS idx_pv_id ON adh_prompt_versions(prompt_id);

-- ============================================================================
-- 工作流配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workflow_configs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    description         TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1,
    is_default          INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    created_by          TEXT
);

-- ============================================================================
-- 工作流步骤表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workflow_steps (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id         INTEGER NOT NULL,
    step_type           TEXT NOT NULL,
    step_name           TEXT NOT NULL,
    step_order          INTEGER NOT NULL,
    max_rounds          INTEGER NOT NULL DEFAULT 1,
    is_enabled          INTEGER NOT NULL DEFAULT 1,
    prompt_key          TEXT,
    config              TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ws_workflow ON adh_workflow_steps(workflow_id);

-- ============================================================================
-- 工作流执行日志表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workflow_logs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id         INTEGER NOT NULL,
    workflow_name       TEXT,
    session_id          TEXT NOT NULL,
    user_id             INTEGER,
    username            TEXT,
    question            TEXT,
    current_step        TEXT,
    current_round       INTEGER,
    metadata_context    TEXT,
    metadata_requested  TEXT,
    metadata_supplemented TEXT,
    llm_analysis        TEXT,
    generated_sql       TEXT,
    execution_result    TEXT,
    analysis_result     TEXT,
    chart_type          TEXT,
    status              TEXT NOT NULL DEFAULT 'running',
    error_message       TEXT,
    started_at          TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at        TEXT,
    elapsed_ms          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_wl_workflow ON adh_workflow_logs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_wl_session ON adh_workflow_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_wl_status ON adh_workflow_logs(status);

-- ============================================================================
-- SQL纠错学习表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_sql_corrections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource_id   INTEGER NOT NULL DEFAULT 0,
    question        TEXT NOT NULL,
    failed_sql      TEXT NOT NULL,
    error_message   TEXT NOT NULL,
    corrected_sql   TEXT NOT NULL,
    tables_used     TEXT DEFAULT '',
    correction_type TEXT DEFAULT 'auto',
    status          TEXT DEFAULT 'pending',
    usage_count     INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    embedding       TEXT
);

CREATE INDEX IF NOT EXISTS idx_sc_status ON adh_sql_corrections(status);

-- ============================================================================
-- Pipeline 执行指标表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_pipeline_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    username        TEXT DEFAULT '',
    question        TEXT DEFAULT '',
    requested_mode  TEXT NOT NULL DEFAULT 'auto',
    resolved_mode   TEXT NOT NULL DEFAULT 'auto',
    fallback_used   INTEGER DEFAULT 0,
    success         INTEGER DEFAULT 1,
    elapsed_ms      INTEGER DEFAULT 0,
    stage_timings   TEXT,
    token_count     TEXT,
    error_message   TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pm_user ON adh_pipeline_metrics(user_id);
CREATE INDEX IF NOT EXISTS idx_pm_created ON adh_pipeline_metrics(created_at);

-- ============================================================================
-- MCP 服务器配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_mcp_servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    transport TEXT NOT NULL DEFAULT 'sse',
    url TEXT DEFAULT '',
    command TEXT DEFAULT '',
    docker_image TEXT DEFAULT '',
    args TEXT DEFAULT '',
    env TEXT,
    tools_config TEXT,
    discovered_tools TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    datasource_id INTEGER DEFAULT 0,
    last_test_at TEXT,
    last_test_status TEXT,
    last_test_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- Agent 配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    agent_type TEXT NOT NULL DEFAULT 'custom',
    system_prompt TEXT,
    mcp_server_ids TEXT DEFAULT '',
    datasource_ids TEXT DEFAULT '',
    tools TEXT,
    config TEXT,
    route_patterns TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- Skills 管理表（分析技能 / 提示词模板）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER DEFAULT 0,
    name TEXT NOT NULL,
    display_name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    category TEXT DEFAULT 'analysis',
    system_prompt TEXT,
    skill_config TEXT,
    source_type TEXT DEFAULT 'user',
    source_skill TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- MCP 服务注册表（市场目录）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_mcp_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    package_name TEXT NOT NULL,
    description TEXT,
    author TEXT DEFAULT '',
    homepage TEXT DEFAULT '',
    install_type TEXT NOT NULL DEFAULT 'npm',
    install_cmd TEXT DEFAULT '',
    default_args TEXT,
    required_env TEXT,
    category TEXT NOT NULL DEFAULT 'other',
    tags TEXT DEFAULT '',
    logo_url TEXT DEFAULT '',
    stars INTEGER DEFAULT 0,
    downloads INTEGER DEFAULT 0,
    is_verified INTEGER DEFAULT 0,
    is_popular INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- LLM 模型配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_llm_models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'anthropic',
    model_id        TEXT NOT NULL,
    model_name      TEXT NOT NULL DEFAULT '',
    base_url        TEXT DEFAULT '',
    api_key         TEXT DEFAULT '',
    max_tokens      INTEGER DEFAULT 4096,
    context_window  INTEGER DEFAULT 200000,
    temperature     REAL DEFAULT 0.0,
    supports_thinking INTEGER DEFAULT 1,
    is_default      INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================================
-- 默认管理员用户 (密码: admin123)
-- ============================================================================
INSERT OR IGNORE INTO adh_users (id, username, password_hash, user_role, status)
VALUES (1, 'admin', '$2b$12$LJ3m4ys3Lz0YBNOURq0Y3OjCfKJmKPOJYqDTPVCKzLOBhZMHfWO6e', 'admin', 'active');

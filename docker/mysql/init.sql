-- AI-DataHub Tables (MySQL 8.0+ compatible)
-- This script creates all tables with adh_ prefix for AI-DataHub.
-- Note: Vector search (HNSW) is not available in MySQL; embedding columns stored as JSON.

CREATE DATABASE IF NOT EXISTS adh DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE adh;

-- ============================================================================
-- 数据源配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_datasources (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    name            VARCHAR(128) NOT NULL,
    db_type         VARCHAR(32)  NOT NULL DEFAULT 'mysql',
    host            VARCHAR(256) NOT NULL,
    port            INT          NOT NULL DEFAULT 3306,
    username        VARCHAR(128) NOT NULL,
    password        VARCHAR(256) NOT NULL,
    database_name   VARCHAR(128) DEFAULT '',
    is_default      TINYINT      DEFAULT 0,
    ssl             TINYINT      DEFAULT 0,
    owner_id        BIGINT       NOT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB;

-- ============================================================================
-- 表级元数据
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_table_info (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    datasource_id       BIGINT NOT NULL DEFAULT 0,
    table_name          VARCHAR(128) NOT NULL,
    table_comment       VARCHAR(512) DEFAULT '',
    table_business_desc VARCHAR(1024) DEFAULT '',
    keywords            VARCHAR(512) DEFAULT '',
    region_tag          VARCHAR(16)  DEFAULT '',
    domain_tag          VARCHAR(64)  DEFAULT '',
    is_active           TINYINT      DEFAULT 1,
    sync_time           DATETIME     NOT NULL,
    embedding           JSON,
    PRIMARY KEY (id),
    INDEX idx_ds_table (datasource_id, table_name),
    INDEX idx_active (is_active)
) ENGINE=InnoDB;

-- ============================================================================
-- 字段级元数据
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_column_metadata (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    table_name      VARCHAR(128) NOT NULL,
    column_name     VARCHAR(128) NOT NULL,
    data_type       VARCHAR(64)  NOT NULL,
    column_comment  VARCHAR(512) DEFAULT '',
    business_desc   VARCHAR(1024) DEFAULT '',
    is_key          VARCHAR(8)   DEFAULT 'false',
    is_nullable     VARCHAR(8)   DEFAULT 'YES',
    is_active       TINYINT      DEFAULT 1,
    sync_time       DATETIME     NOT NULL,
    embedding       JSON,
    PRIMARY KEY (id),
    INDEX idx_ds_table_col (datasource_id, table_name, column_name),
    INDEX idx_active (is_active)
) ENGINE=InnoDB;

-- ============================================================================
-- SQL 模板表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_sql_templates (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    template_id     VARCHAR(64)  NOT NULL,
    template_name   VARCHAR(256) NOT NULL,
    category        VARCHAR(64)  NOT NULL,
    intent_keywords VARCHAR(512) NOT NULL,
    sql_template    TEXT         NOT NULL,
    variables       TEXT,
    rules           TEXT,
    description     VARCHAR(512) DEFAULT '',
    usage_count     INT          DEFAULT 0,
    is_active       TINYINT      DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    embedding       JSON,
    PRIMARY KEY (id),
    UNIQUE KEY uk_template_id (template_id),
    INDEX idx_active (is_active)
) ENGINE=InnoDB;

-- ============================================================================
-- 业务术语表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_business_terms (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    term_cn         VARCHAR(128) NOT NULL,
    term_en         VARCHAR(128) DEFAULT '',
    term_aliases    VARCHAR(512) DEFAULT '',
    term_type       VARCHAR(32)  NOT NULL,
    target_table    VARCHAR(128) DEFAULT '',
    target_column   VARCHAR(128) DEFAULT '',
    calculation     TEXT,
    description     VARCHAR(512) DEFAULT '',
    usage_count     INT          DEFAULT 0,
    is_active       TINYINT      DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    embedding       JSON,
    PRIMARY KEY (id),
    INDEX idx_term_cn (term_cn),
    INDEX idx_active (is_active)
) ENGINE=InnoDB;

-- ============================================================================
-- 查询审计表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_query_audit (
    id                BIGINT NOT NULL AUTO_INCREMENT,
    datasource_id     BIGINT NOT NULL DEFAULT 0,
    user_id           VARCHAR(64)  NOT NULL,
    username          VARCHAR(64)  NOT NULL,
    user_role         VARCHAR(32)  NOT NULL,
    question          TEXT         NOT NULL,
    generated_sql     TEXT,
    query_type        VARCHAR(16)  DEFAULT 'sql',
    execution_status  VARCHAR(32)  NOT NULL,
    row_count         INT          DEFAULT 0,
    execution_time_ms INT          DEFAULT 0,
    error_message     TEXT,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_user (user_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB;

-- ============================================================================
-- 仪表盘表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_dashboards (
    id                BIGINT NOT NULL AUTO_INCREMENT,
    name              VARCHAR(256) NOT NULL,
    description       TEXT,
    layout            TEXT,
    filters           TEXT,
    params            TEXT,
    status            VARCHAR(32)  DEFAULT 'designing',
    owner_id          BIGINT       NOT NULL,
    is_public         TINYINT      DEFAULT 0,
    is_default        TINYINT      DEFAULT 0,
    carousel_interval INT          DEFAULT 0,
    sort_order        INT          DEFAULT 0,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB;

-- ============================================================================
-- 图表表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_charts (
    id            BIGINT NOT NULL AUTO_INCREMENT,
    dashboard_id  BIGINT       NOT NULL,
    name          VARCHAR(256) NOT NULL,
    chart_type    VARCHAR(32)  NOT NULL,
    sql_query     TEXT,
    config        TEXT,
    position      TEXT,
    source_type   VARCHAR(32)  DEFAULT 'query',
    source_id     BIGINT       NULL,
    data_cache    TEXT,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_dashboard (dashboard_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 图表快照表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_chart_snapshots (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    user_id         BIGINT       NOT NULL,
    question        VARCHAR(512) NULL,
    sql_query       TEXT,
    chart_type      VARCHAR(32)  NULL,
    brief           VARCHAR(256) NULL,
    columns         TEXT,
    data_snapshot   TEXT,
    row_count       INT          NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_user (user_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 对话表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_conversations (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    title           VARCHAR(256) NOT NULL DEFAULT '新对话',
    user_id         BIGINT       NOT NULL,
    embed_user_id   VARCHAR(128) DEFAULT '',
    datasource_id   BIGINT       NOT NULL DEFAULT 0,
    messages        LONGTEXT,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_user (user_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 对话消息表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_messages (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    conversation_id BIGINT NOT NULL,
    role            VARCHAR(32)  NOT NULL,
    content         TEXT         NOT NULL,
    sql_query       TEXT,
    warnings        JSON,
    thinking        TEXT,
    rag             JSON,
    result          JSON,
    error           TEXT,
    feedback        VARCHAR(32)  DEFAULT '',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_msg_conv (conversation_id),
    INDEX idx_msg_created (created_at)
) ENGINE=InnoDB;

-- ============================================================================
-- 保存的查询 / 数据集表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_saved_queries (
    id                BIGINT NOT NULL AUTO_INCREMENT,
    name              VARCHAR(256) NOT NULL,
    description       TEXT,
    sql_query         TEXT         NOT NULL,
    is_dataset        TINYINT      DEFAULT 0,
    dataset_keywords  VARCHAR(512) DEFAULT '',
    owner_id          BIGINT       NOT NULL,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB;

-- ============================================================================
-- 菜单树
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_menu_items (
    id          BIGINT       NOT NULL AUTO_INCREMENT,
    parent_id   BIGINT       DEFAULT NULL,
    name        VARCHAR(255) NOT NULL,
    icon        VARCHAR(50)  DEFAULT '',
    page_id     BIGINT       DEFAULT NULL,
    link_type   VARCHAR(20)  DEFAULT 'page',
    is_system   TINYINT      DEFAULT 0,
    sort_order  INT          DEFAULT 0,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB;

-- ============================================================================
-- 表关联关系表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_table_relations (
    id                BIGINT NOT NULL AUTO_INCREMENT,
    datasource_id     BIGINT NOT NULL DEFAULT 0,
    source_table      VARCHAR(128) NOT NULL,
    source_column     VARCHAR(128) NOT NULL,
    target_table      VARCHAR(128) NOT NULL,
    target_column     VARCHAR(128) NOT NULL,
    relation_type     VARCHAR(16)  DEFAULT '1:N',
    join_type         VARCHAR(16)  DEFAULT 'INNER',
    description       VARCHAR(512) DEFAULT '',
    is_active         TINYINT      DEFAULT 1,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    embedding         JSON,
    PRIMARY KEY (id),
    INDEX idx_source (source_table),
    INDEX idx_target (target_table),
    INDEX idx_active (is_active)
) ENGINE=InnoDB;

-- ============================================================================
-- 用户表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_users (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    username        VARCHAR(64)  NOT NULL,
    password_hash   VARCHAR(128) NOT NULL,
    email           VARCHAR(128) DEFAULT '',
    phone           VARCHAR(20)  DEFAULT '',
    avatar          VARCHAR(512) DEFAULT '',
    user_role       VARCHAR(32)  NOT NULL DEFAULT 'viewer',
    status          VARCHAR(16)  NOT NULL DEFAULT 'active',
    last_login      DATETIME     NULL,
    login_attempts  INT          DEFAULT 0,
    locked_until    DATETIME     NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_username (username)
) ENGINE=InnoDB;

-- ============================================================================
-- 操作审计日志表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_audit_logs (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    user_id         BIGINT       NOT NULL,
    username        VARCHAR(64)  NOT NULL,
    action          VARCHAR(64)  NOT NULL,
    module          VARCHAR(32)  DEFAULT '' COMMENT '操作模块: datasource/metadata/model/workspace/agent/scheduled_task/system',
    target_type     VARCHAR(32)  DEFAULT '',
    target_id       BIGINT       DEFAULT 0,
    detail          TEXT,
    ip_address      VARCHAR(64)  DEFAULT '',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_user (user_id),
    INDEX idx_created (created_at),
    INDEX idx_module (module)
) ENGINE=InnoDB;

-- ============================================================================
-- 第三方应用表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_applications (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    name                VARCHAR(128) NOT NULL,
    app_key_hash        VARCHAR(256) NOT NULL,
    status              VARCHAR(16)  DEFAULT 'active',
    enable_chat         TINYINT      DEFAULT 1,
    allowed_dashboards  TEXT,
    allowed_tables      TEXT,
    rate_limit          INT          DEFAULT 60,
    description         VARCHAR(512) DEFAULT '',
    last_used_at        DATETIME     NULL,
    created_by          BIGINT       NOT NULL,
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB;

-- ============================================================================
-- 嵌入调用日志表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_embed_logs (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    app_id          BIGINT       NOT NULL,
    user_id         VARCHAR(128) NOT NULL,
    user_name       VARCHAR(128) DEFAULT '',
    action          VARCHAR(64)  NOT NULL,
    detail          TEXT,
    ip_address      VARCHAR(64)  DEFAULT '',
    status          VARCHAR(16)  DEFAULT 'success',
    error_message   TEXT,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_app (app_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB;

-- ============================================================================
-- 系统配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_system_config (
    config_key      VARCHAR(128) NOT NULL,
    config_value    TEXT         NOT NULL,
    description     VARCHAR(512) DEFAULT '',
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (config_key)
) ENGINE=InnoDB;

-- 默认配置
INSERT IGNORE INTO adh_system_config (config_key, config_value, description, updated_at)
VALUES ('max_interpretation_rounds', '3', '分析结果解读最大LLM轮数', NOW());

INSERT IGNORE INTO adh_system_config (config_key, config_value, description, updated_at)
VALUES ('enable_metadata_supplementation', '1', '启用元数据补充功能（Loop Engineering）', NOW());

-- ============================================================================
-- Prompt管理表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_prompts (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    prompt_key          VARCHAR(100) NOT NULL,
    prompt_name         VARCHAR(200) NOT NULL,
    system_prompt       TEXT,
    user_prompt_template TEXT,
    description         TEXT,
    version             INT NOT NULL DEFAULT 1,
    is_active           TINYINT NOT NULL DEFAULT 1,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by          VARCHAR(100),
    change_log          TEXT,
    PRIMARY KEY (id),
    UNIQUE KEY uk_prompt_key (prompt_key)
) ENGINE=InnoDB;

-- ============================================================================
-- Prompt版本历史表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_prompt_versions (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    prompt_id           BIGINT NOT NULL,
    prompt_key          VARCHAR(100) NOT NULL,
    version             INT NOT NULL,
    system_prompt       TEXT,
    user_prompt_template TEXT,
    change_log          TEXT,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by          VARCHAR(100),
    is_current          TINYINT NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    INDEX idx_prompt_key (prompt_key),
    INDEX idx_prompt_id (prompt_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 工作流配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workflow_configs (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    name                VARCHAR(100) NOT NULL,
    description         TEXT,
    is_active           TINYINT NOT NULL DEFAULT 1,
    is_default          TINYINT NOT NULL DEFAULT 0,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by          VARCHAR(100),
    PRIMARY KEY (id)
) ENGINE=InnoDB;

-- ============================================================================
-- 工作流步骤表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workflow_steps (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    workflow_id         BIGINT NOT NULL,
    step_type           VARCHAR(50) NOT NULL,
    step_name           VARCHAR(100) NOT NULL,
    step_order          INT NOT NULL,
    max_rounds          INT NOT NULL DEFAULT 1,
    is_enabled          TINYINT NOT NULL DEFAULT 1,
    prompt_key          VARCHAR(100),
    config              TEXT,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_workflow (workflow_id)
) ENGINE=InnoDB;

-- ============================================================================
-- 工作流执行日志表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workflow_logs (
    id                  BIGINT NOT NULL AUTO_INCREMENT,
    workflow_id         BIGINT NOT NULL,
    workflow_name       VARCHAR(100),
    session_id          VARCHAR(100) NOT NULL,
    user_id             BIGINT,
    username            VARCHAR(64),
    question            TEXT,
    current_step        VARCHAR(50),
    current_round       INT,
    metadata_context    LONGTEXT,
    metadata_requested  TEXT,
    metadata_supplemented TEXT,
    llm_analysis        TEXT,
    generated_sql       TEXT,
    execution_result    LONGTEXT,
    analysis_result     TEXT,
    chart_type          VARCHAR(32),
    status              VARCHAR(20) NOT NULL DEFAULT 'running',
    error_message       TEXT,
    started_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at        DATETIME,
    elapsed_ms          INT,
    PRIMARY KEY (id),
    INDEX idx_workflow (workflow_id),
    INDEX idx_session (session_id),
    INDEX idx_status (status)
) ENGINE=InnoDB;

-- ============================================================================
-- SQL纠错学习表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_sql_corrections (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    question        TEXT NOT NULL,
    failed_sql      TEXT NOT NULL,
    error_message   TEXT NOT NULL,
    corrected_sql   TEXT NOT NULL,
    tables_used     VARCHAR(512) DEFAULT '',
    correction_type VARCHAR(32) DEFAULT 'auto',
    status          VARCHAR(16) DEFAULT 'pending',
    usage_count     INT DEFAULT 0,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    embedding       JSON,
    PRIMARY KEY (id),
    INDEX idx_status (status)
) ENGINE=InnoDB;

-- ============================================================================
-- Pipeline 执行指标表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_pipeline_metrics (
    id              BIGINT       NOT NULL AUTO_INCREMENT,
    user_id         BIGINT       NOT NULL,
    username        VARCHAR(64)  DEFAULT '',
    question        VARCHAR(256) DEFAULT '',
    requested_mode  VARCHAR(16)  NOT NULL DEFAULT 'auto',
    resolved_mode   VARCHAR(16)  NOT NULL DEFAULT 'auto',
    fallback_used   TINYINT      DEFAULT 0,
    success         TINYINT      DEFAULT 1,
    elapsed_ms      INT          DEFAULT 0,
    stage_timings   TEXT,
    token_count     TEXT,
    error_message   VARCHAR(512) DEFAULT '',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_user (user_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB;

-- ============================================================================
-- MCP 服务器配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_mcp_servers (
    id BIGINT NOT NULL AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    description VARCHAR(500) DEFAULT '',
    transport VARCHAR(20) NOT NULL DEFAULT 'sse',
    url VARCHAR(500) DEFAULT '',
    command VARCHAR(200) DEFAULT '',
    docker_image VARCHAR(200) DEFAULT '',
    args VARCHAR(1000) DEFAULT '',
    `env` TEXT,
    tools_config TEXT,
    discovered_tools TEXT,
    is_active TINYINT NOT NULL DEFAULT 1,
    datasource_id BIGINT DEFAULT 0,
    last_test_at DATETIME,
    last_test_status VARCHAR(20),
    last_test_message VARCHAR(500),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB;

-- ============================================================================
-- Agent 配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_agents (
    id BIGINT NOT NULL AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(100) DEFAULT '',
    description VARCHAR(500) DEFAULT '',
    agent_type VARCHAR(20) NOT NULL DEFAULT 'custom',
    system_prompt TEXT,
    mcp_server_ids VARCHAR(500) DEFAULT '',
    datasource_ids VARCHAR(500) DEFAULT '',
    tools TEXT,
    config TEXT,
    route_patterns TEXT,
    is_active TINYINT NOT NULL DEFAULT 1,
    is_default TINYINT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB;

-- ============================================================================
-- Skills 管理表（分析技能 / 提示词模板）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_skills (
    id BIGINT NOT NULL AUTO_INCREMENT,
    workspace_id BIGINT DEFAULT 0,
    name VARCHAR(100) NOT NULL,
    display_name VARCHAR(100) DEFAULT '',
    description VARCHAR(500) DEFAULT '',
    category VARCHAR(50) DEFAULT 'analysis',
    system_prompt TEXT,
    skill_config TEXT,
    source_type VARCHAR(20) DEFAULT 'user' COMMENT 'system = file-based, user = user-created',
    source_skill VARCHAR(100) DEFAULT '' COMMENT 'copied from which system skill',
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_name (name)
) ENGINE=InnoDB;

-- ============================================================================
-- MCP 服务注册表（市场目录）
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_mcp_registry (
    id BIGINT NOT NULL AUTO_INCREMENT,
    name VARCHAR(200) NOT NULL,
    package_name VARCHAR(500) NOT NULL,
    description TEXT,
    author VARCHAR(100) DEFAULT '',
    homepage VARCHAR(500) DEFAULT '',
    install_type VARCHAR(20) NOT NULL DEFAULT 'npm',
    install_cmd VARCHAR(500) DEFAULT '',
    default_args TEXT,
    required_env TEXT,
    category VARCHAR(50) NOT NULL DEFAULT 'other',
    tags VARCHAR(500) DEFAULT '',
    logo_url VARCHAR(500) DEFAULT '',
    stars INT DEFAULT 0,
    downloads INT DEFAULT 0,
    is_verified TINYINT DEFAULT 0,
    is_popular TINYINT DEFAULT 0,
    sort_order INT DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB;

-- ============================================================================
-- LLM 模型配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_llm_models (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    name            VARCHAR(100) NOT NULL,
    provider        VARCHAR(50)  NOT NULL DEFAULT 'anthropic',
    model_id        VARCHAR(100) NOT NULL,
    model_name      VARCHAR(100) NOT NULL DEFAULT '',
    base_url        VARCHAR(256) DEFAULT '',
    api_key         VARCHAR(256) DEFAULT '',
    max_tokens      INT          DEFAULT 4096,
    context_window  INT          DEFAULT 200000,
    temperature     FLOAT        DEFAULT 0.0,
    supports_thinking TINYINT    DEFAULT 1,
    is_default      TINYINT      DEFAULT 0,
    is_active       TINYINT      DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB;

-- ============================================================================
-- 默认管理员用户 (密码: admin123)
-- ============================================================================
INSERT IGNORE INTO adh_users (id, username, password_hash, user_role, status, created_at, updated_at)
VALUES (1, 'admin', '$2b$12$LJ3m4ys3Lz0YBNOURq0Y3OjCfKJmKPOJYqDTPVCKzLOBhZMHfWO6e', 'admin', 'active', NOW(), NOW());

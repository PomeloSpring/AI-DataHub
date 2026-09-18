-- Observability Migration (O0) — LLM 交互可观测地基
-- 存储: MySQL 元库(O0 先只用 MySQL;Doris 分区留后续)
-- 风格对齐 docker/mysql/*_migration.sql;表用 IF NOT EXISTS 可重复执行。
-- 列与 services/shared/observability/store.py 严格一致。

USE adh2;

-- ============================================================================
-- LLM 回合 trace 摘要(一次用户回合一轨迹)
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_llm_traces (
    trace_id        VARCHAR(64)  NOT NULL,
    message_uuid    VARCHAR(64)  DEFAULT '',        -- 助手回合稳定标识(赞踩/关联主键)
    conversation_id BIGINT       NOT NULL DEFAULT 0,
    user_id         BIGINT       NOT NULL DEFAULT 0,
    username        VARCHAR(128) DEFAULT '',
    user_role       VARCHAR(64)  DEFAULT '',
    workspace_id    BIGINT       NOT NULL DEFAULT 0,
    datasource_id   BIGINT       NOT NULL DEFAULT 0,
    entrypoint      VARCHAR(32)  DEFAULT 'chat',    -- chat/playground/agent/scheduled/embed/report
    model_ref       VARCHAR(191) DEFAULT '',
    question        MEDIUMTEXT,
    final_answer    MEDIUMTEXT,
    status          VARCHAR(16)  DEFAULT 'success', -- success/error/cancelled
    error_message   TEXT,
    started_at      DATETIME(3)  NULL,
    duration_ms     BIGINT       NOT NULL DEFAULT 0,
    input_tokens    INT          NOT NULL DEFAULT 0,
    output_tokens   INT          NOT NULL DEFAULT 0,
    total_tokens    INT          NOT NULL DEFAULT 0,
    credits         DECIMAL(18,6) NULL,             -- 本轮各次请求级 credit 之和(Qoder)
    cost_usd        DECIMAL(18,6) NULL,             -- 兜底估算(直连 token×单价)
    session_credits DECIMAL(18,6) NULL,             -- 会话累计(Result.total_credits, 不与他处相加)
    llm_call_count  INT          NOT NULL DEFAULT 0,
    span_count      INT          NOT NULL DEFAULT 0,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trace_id),
    INDEX idx_msg (message_uuid),
    INDEX idx_user_time (user_id, started_at),
    INDEX idx_conv (conversation_id),
    INDEX idx_model_time (model_ref, started_at),
    INDEX idx_ws_time (workspace_id, started_at),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================================
-- LLM span 明细(O0 仅 kind=llm_call;其余 kind 在 O1 复用同表)
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_llm_spans (
    span_id          VARCHAR(64)  NOT NULL,
    trace_id         VARCHAR(64)  NOT NULL,
    kind             VARCHAR(32)  NOT NULL DEFAULT 'llm_call',
    name             VARCHAR(191) DEFAULT '',
    status           VARCHAR(16)  DEFAULT 'success',
    duration_ms      BIGINT       NOT NULL DEFAULT 0,
    model_ref        VARCHAR(191) DEFAULT '',
    input_tokens     INT          NOT NULL DEFAULT 0,
    output_tokens    INT          NOT NULL DEFAULT 0,
    total_tokens     INT          NOT NULL DEFAULT 0,
    credits          DECIMAL(18,6) NULL,            -- 请求级 credit(Assistant.usage)
    original_credits DECIMAL(18,6) NULL,
    billable         TINYINT      NULL,
    cost_usd         DECIMAL(18,6) NULL,
    input_text       MEDIUMTEXT,
    output_text      MEDIUMTEXT,
    error_text       TEXT,
    dt               DATE         NULL,
    started_at       DATETIME(3)  NULL,
    PRIMARY KEY (span_id),
    INDEX idx_trace (trace_id),
    INDEX idx_dt (dt)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================================
-- 消息赞踩反馈(统一主键;写入口在 O3,此处先建表)
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_message_feedback (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    trace_id        VARCHAR(64)  NOT NULL DEFAULT '',
    conversation_id BIGINT       NOT NULL DEFAULT 0,
    message_uuid    VARCHAR(64)  NOT NULL,
    user_id         BIGINT       NOT NULL DEFAULT 0,
    workspace_id    BIGINT       NOT NULL DEFAULT 0,
    satisfied       TINYINT      NOT NULL,          -- 1赞/0踩
    expected_table  VARCHAR(128) DEFAULT '',
    reason          TEXT,
    tags            JSON NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_msg (message_uuid),
    INDEX idx_trace (trace_id),
    INDEX idx_sat (satisfied)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================================
-- 模型单价(仅非 Qoder 直连路径兜底估算用;可空。ALTER 仅执行一次)
-- ============================================================================
ALTER TABLE adh_llm_models
    ADD COLUMN input_price_per_1k  DECIMAL(12,6) NULL,
    ADD COLUMN output_price_per_1k DECIMAL(12,6) NULL,
    ADD COLUMN currency            VARCHAR(8)    NULL DEFAULT 'USD';

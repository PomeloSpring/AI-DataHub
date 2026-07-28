-- ═══════════════════════════════════════════════════════════════
-- 质量评审模块 — MySQL 版（客观指标 + 手动 LLM 评审）
-- 执行: mysql -u root -p < quality_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh;

CREATE TABLE IF NOT EXISTS adh_quality_reviews (
    id              BIGINT PRIMARY KEY,
    workspace_id    BIGINT NOT NULL DEFAULT 0,
    conversation_id BIGINT DEFAULT 0,
    message_id      BIGINT DEFAULT 0,
    user_id         BIGINT NOT NULL,
    username        VARCHAR(64) DEFAULT '',
    question        TEXT NOT NULL,
    generated_sql   TEXT,
    datasource_id   BIGINT DEFAULT 0,
    -- 客观指标
    execution_status VARCHAR(16) DEFAULT 'success' COMMENT 'success / error / empty',
    row_count       INT DEFAULT 0,
    elapsed_ms      INT DEFAULT 0,
    retry_count     INT DEFAULT 0,
    pipeline_mode   VARCHAR(16) DEFAULT '' COMMENT 'quick / deep / agent',
    -- 用户反馈
    user_feedback   TINYINT COMMENT '1=满意, 0=不满意, NULL=未反馈',
    -- 手动 LLM 评审
    llm_review      TEXT COMMENT 'LLM 评审意见（手动触发）',
    llm_reviewed_at DATETIME,
    -- 状态
    status          VARCHAR(32) DEFAULT 'recorded',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace (workspace_id),
    INDEX idx_user (user_id),
    INDEX idx_status (execution_status),
    INDEX idx_mode (pipeline_mode),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质量评审记录';

-- 质量统计快照
CREATE TABLE IF NOT EXISTS adh_quality_stats (
    id              BIGINT PRIMARY KEY,
    workspace_id    BIGINT NOT NULL DEFAULT 0,
    stat_date       DATE NOT NULL,
    total_queries   INT DEFAULT 0,
    avg_score       DECIMAL(5,2) COMMENT '成功率或平均分',
    score_distribution JSON,
    issue_top_tags  JSON,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_workspace_date (workspace_id, stat_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质量统计快照';

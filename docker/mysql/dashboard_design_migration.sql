-- AS-BOT 仪表盘设计：草稿为共享事实源，发布复用审批流水。
CREATE TABLE IF NOT EXISTS adh_as_bot_dashboard_designs (
    id CHAR(32) NOT NULL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    conversation_id BIGINT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'awaiting_selection',
    content JSON NOT NULL,
    preview JSON NULL,
    approval_id BIGINT NULL,
    result JSON NULL,
    preview_expires_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    INDEX idx_design_conversation (user_id, conversation_id),
    UNIQUE KEY uk_design_approval (approval_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE adh_as_bot_approvals MODIFY COLUMN status
    ENUM('pending','approved','executing','rejected','executed','failed','superseded') DEFAULT 'pending';

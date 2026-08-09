-- Chat Attachments Migration
-- 多模态处理能力:聊天附件(图片/表格/文档/3D模型)元数据表
-- 文件本体存储在本地磁盘(ADH_UPLOAD_DIR,默认 data/chat_attachments/)

USE adh;

-- ============================================================================
-- 1. 聊天附件表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_chat_attachments (
    id              VARCHAR(32)  NOT NULL COMMENT '附件ID(uuid hex)',
    user_id         BIGINT       NOT NULL,
    workspace_id    BIGINT       DEFAULT 0,
    filename        VARCHAR(255) NOT NULL COMMENT '原始文件名',
    mime_type       VARCHAR(100) DEFAULT '',
    category        VARCHAR(20)  NOT NULL COMMENT 'image | table | document | model3d',
    storage_path    VARCHAR(512) NOT NULL COMMENT '服务端存储绝对路径',
    size            BIGINT       DEFAULT 0 COMMENT '文件大小(字节)',
    parsed_meta     JSON         COMMENT '解析结果缓存(表格结构/文档摘要等)',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_user (user_id),
    INDEX idx_workspace (workspace_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================================
-- 2. LLM 模型表增加视觉能力标记(幂等)
-- ============================================================================
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_llm_models'
      AND COLUMN_NAME = 'supports_vision'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_llm_models ADD COLUMN supports_vision TINYINT DEFAULT 1 COMMENT ''是否支持图片多模态输入''',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

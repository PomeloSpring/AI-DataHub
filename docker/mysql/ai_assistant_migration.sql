-- AI助手数据库迁移脚本
-- 执行方式: mysql -u root -p < ai_assistant_migration.sql

-- ============================================================================
-- 知识库文档表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_knowledge_documents (
    id                VARCHAR(64)  NOT NULL,
    title             VARCHAR(256) NOT NULL,
    content           TEXT         NOT NULL,
    doc_type          VARCHAR(32)  DEFAULT 'guide',
    source            VARCHAR(64)  DEFAULT 'manual',
    file_path         VARCHAR(512) DEFAULT '',
    file_size         INT          DEFAULT 0,
    chunk_count       INT          DEFAULT 0,
    status            VARCHAR(16)  DEFAULT 'active',
    tags              JSON,
    metadata          JSON,
    created_by        INT          DEFAULT 0,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_doc_type (doc_type),
    INDEX idx_source (source),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB;

-- ============================================================================
-- 知识库分块表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_knowledge_chunks (
    id                VARCHAR(64)  NOT NULL,
    document_id       VARCHAR(64)  NOT NULL,
    chunk_index       INT          NOT NULL DEFAULT 0,
    content           TEXT         NOT NULL,
    chunk_size        INT          DEFAULT 0,
    metadata          JSON,
    embedding         JSON,
    is_active         TINYINT      DEFAULT 1,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_document_id (document_id),
    INDEX idx_is_active (is_active),
    INDEX idx_chunk_index (chunk_index)
) ENGINE=InnoDB;

-- ============================================================================
-- 对话历史表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_conversation_history (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    session_id        VARCHAR(64)  NOT NULL,
    user_id           INT          NOT NULL,
    role              VARCHAR(16)  NOT NULL,
    content           TEXT         NOT NULL,
    metadata          JSON,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    INDEX idx_session_id (session_id),
    INDEX idx_user_id (user_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB;

-- ============================================================================
-- 知识库同步日志表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_knowledge_sync_logs (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    source            VARCHAR(64)  NOT NULL,
    status            VARCHAR(16)  NOT NULL DEFAULT 'running',
    document_count    INT          DEFAULT 0,
    chunk_count       INT          DEFAULT 0,
    error_message     TEXT,
    started_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      DATETIME,
    PRIMARY KEY (id),
    INDEX idx_source (source),
    INDEX idx_status (status),
    INDEX idx_started_at (started_at)
) ENGINE=InnoDB;

-- ============================================================================
-- 页面上下文配置表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_page_contexts (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    page_key          VARCHAR(64)  NOT NULL,
    module            VARCHAR(64)  NOT NULL,
    sub_module        VARCHAR(64)  DEFAULT '',
    title             VARCHAR(128) NOT NULL,
    description       VARCHAR(512) DEFAULT '',
    common_questions  JSON,
    quick_actions     JSON,
    related_docs      JSON,
    is_active         TINYINT      DEFAULT 1,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE INDEX uk_page_key (page_key),
    INDEX idx_module (module),
    INDEX idx_is_active (is_active)
) ENGINE=InnoDB;

-- ============================================================================
-- 初始化页面上下文数据
-- ============================================================================
INSERT INTO adh_page_contexts (page_key, module, sub_module, title, description, common_questions, quick_actions) VALUES
('datasource', 'admin', 'datasource', '数据源管理', '配置和管理数据库连接',
 '["如何配置MySQL数据源？", "数据源连接失败怎么办？", "如何测试数据源连接？", "支持哪些数据库类型？"]',
 '[{"label": "新建数据源", "icon": "➕", "action": "create"}, {"label": "测试连接", "icon": "🔌", "action": "test"}, {"label": "查看文档", "icon": "📖", "action": "docs"}]'),

('agent', 'admin', 'agent', 'Agent管理', '配置和管理AI代理',
 '["什么是Agent？", "如何配置Agent？", "Agent的工作原理是什么？", "如何优化Agent性能？"]',
 '[{"label": "新建Agent", "icon": "➕", "action": "create"}, {"label": "配置提示词", "icon": "📝", "action": "prompt"}, {"label": "查看日志", "icon": "📋", "action": "logs"}]'),

('workflow', 'admin', 'workflow', '工作流配置', '配置和管理工作流',
 '["如何创建工作流？", "工作流节点有哪些类型？", "如何调试工作流？", "工作流支持并行执行吗？"]',
 '[{"label": "新建工作流", "icon": "➕", "action": "create"}, {"label": "导入工作流", "icon": "📥", "action": "import"}, {"label": "查看示例", "icon": "💡", "action": "examples"}]'),

('scheduled-tasks', 'admin', 'scheduled-tasks', '定时任务', '配置和管理定时任务',
 '["如何创建定时任务？", "定时任务支持哪些触发方式？", "如何查看任务执行日志？", "任务执行失败怎么办？"]',
 '[{"label": "新建任务", "icon": "➕", "action": "create"}, {"label": "查看日志", "icon": "📋", "action": "logs"}]'),

('chat', 'chat', '', '数据查询', '自然语言数据查询',
 '["如何提高查询准确性？", "查询结果为空怎么办？", "如何查看生成的SQL？", "支持哪些查询类型？"]',
 '[{"label": "新建查询", "icon": "➕", "action": "create"}, {"label": "查看历史", "icon": "📜", "action": "history"}]'),

('dashboard', 'dashboard', '', '仪表盘', '数据可视化仪表盘',
 '["如何创建仪表盘？", "如何添加图表？", "如何设置自动刷新？", "如何分享仪表盘？"]',
 '[{"label": "新建仪表盘", "icon": "➕", "action": "create"}, {"label": "导入仪表盘", "icon": "📥", "action": "import"}]'),

('history', 'history', '', '查询历史', '查看历史查询记录',
 '["如何查看查询历史？", "如何重新执行查询？", "如何导出查询结果？"]',
 '[{"label": "清空历史", "icon": "🗑️", "action": "clear"}, {"label": "导出历史", "icon": "📤", "action": "export"}]')
ON DUPLICATE KEY UPDATE title = VALUES(title);

-- ============================================================================
-- 完成
-- ============================================================================
SELECT 'AI助手数据库迁移完成' AS status;

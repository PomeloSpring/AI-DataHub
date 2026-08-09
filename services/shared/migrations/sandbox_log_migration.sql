-- 沙箱执行日志表
CREATE TABLE IF NOT EXISTS adh_sandbox_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    sandbox_id BIGINT NOT NULL COMMENT '沙箱 ID',
    sandbox_name VARCHAR(100) DEFAULT '' COMMENT '沙箱名称',
    sandbox_type VARCHAR(20) DEFAULT '' COMMENT '沙箱类型: local/ssh/fc',
    code TEXT NOT NULL COMMENT '执行的代码',
    requirements JSON COMMENT 'pip 依赖列表',
    success TINYINT DEFAULT 0 COMMENT '是否成功',
    stdout TEXT COMMENT '标准输出',
    stderr TEXT COMMENT '标准错误',
    result TEXT COMMENT '返回值',
    error TEXT COMMENT '错误信息',
    elapsed_ms INT DEFAULT 0 COMMENT '耗时(毫秒)',
    conversation_id BIGINT DEFAULT 0 COMMENT '关联对话 ID',
    user_id BIGINT DEFAULT 0 COMMENT '执行用户 ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_sandbox (sandbox_id),
    INDEX idx_user (user_id),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='沙箱执行日志';

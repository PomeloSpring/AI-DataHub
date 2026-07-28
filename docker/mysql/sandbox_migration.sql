-- ═══════════════════════════════════════════════════════════════
-- 沙箱环境管理 — 建表 SQL
-- 执行: mysql -u root -p < sandbox_migration.sql
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS adh_sandbox_environments (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    name            VARCHAR(100) NOT NULL COMMENT '沙箱名称/别名（唯一标识）',
    sandbox_type    VARCHAR(20) NOT NULL COMMENT '沙箱类型: local / ssh / fc',
    display_name    VARCHAR(200) DEFAULT '' COMMENT '显示名称',
    description     TEXT COMMENT '描述',
    config          JSON NOT NULL COMMENT '连接配置（类型不同字段不同）',
    resource_info   JSON COMMENT '资源信息（CPU/内存/GPU，测试时自动探测）',
    status          VARCHAR(20) DEFAULT 'unknown' COMMENT '状态: ready / busy / offline / error / unknown',
    is_default      TINYINT DEFAULT 0 COMMENT '是否默认沙箱',
    is_active       TINYINT DEFAULT 1 COMMENT '是否启用',
    last_heartbeat  DATETIME COMMENT '最后心跳/测试时间',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_name (name),
    INDEX idx_type (sandbox_type),
    INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='沙箱环境配置';

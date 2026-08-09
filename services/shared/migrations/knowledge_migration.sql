-- ═══════════════════════════════════════════════════════════════
-- 知识管理模块 — 建表 SQL
-- 执行: mysql -u root -p < knowledge_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh;

-- 知识条目统一表（4 类知识共用基础结构）
CREATE TABLE IF NOT EXISTS adh_knowledge_items (
    id              BIGINT PRIMARY KEY,
    workspace_id    BIGINT NOT NULL DEFAULT 0 COMMENT '所属工作空间',
    datasource_id   BIGINT DEFAULT 0 COMMENT '关联数据源（0=全局）',
    knowledge_type  VARCHAR(32) NOT NULL COMMENT '知识类型: instruction / sql_pair / recommend_question / followup_case',
    title           VARCHAR(256) NOT NULL COMMENT '标题',
    content         TEXT NOT NULL COMMENT '内容（指令文本 / SQL 对 JSON / 问题文本）',
    metadata        JSON COMMENT '扩展字段（sql_pair 的 question/answer_sql/explanation 等）',
    related_tables  VARCHAR(512) DEFAULT '' COMMENT '关联表名，逗号分隔',
    priority        INT DEFAULT 0 COMMENT '优先级（越高越优先被检索）',
    usage_count     INT DEFAULT 0 COMMENT '被检索命中次数',
    positive_count  INT DEFAULT 0 COMMENT '正面反馈次数',
    negative_count  INT DEFAULT 0 COMMENT '负面反馈次数',
    is_active       TINYINT DEFAULT 1 COMMENT '是否启用',
    embedding       JSON COMMENT '向量嵌入',
    created_by      BIGINT COMMENT '创建人',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_workspace_type (workspace_id, knowledge_type),
    INDEX idx_datasource_type (datasource_id, knowledge_type),
    INDEX idx_active (is_active),
    INDEX idx_priority (priority DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='知识条目统一表';

-- 追问案例专用表（关联主问题 → 追问列表）
CREATE TABLE IF NOT EXISTS adh_followup_cases (
    id              BIGINT PRIMARY KEY,
    knowledge_id    BIGINT NOT NULL COMMENT '关联 adh_knowledge_items.id',
    followup_order  INT NOT NULL DEFAULT 0 COMMENT '追问顺序',
    followup_question TEXT NOT NULL COMMENT '追问问题',
    expected_sql    TEXT COMMENT '期望 SQL',
    expected_result JSON COMMENT '期望结果',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_knowledge (knowledge_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='追问案例';

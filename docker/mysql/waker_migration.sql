-- Waker 统一配置模型 — 以"角色化智能体"为单位配置
-- 替代分散的 adh_agents / adh_skill_templates / prompt 配置
-- 一个 Waker 内联: 职责/风格/边界(persona) + 系统提示词 + 工具集 + MCP + 数据源 + skills + 图表开关
-- MCP、数据源作为共享资源被 Waker 引用(存 ID 列表)

USE adh;

-- ============================================================================
-- 1. Waker 定义表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_wakers (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    waker_key       VARCHAR(64)  NOT NULL COMMENT '唯一英文标识',
    name            VARCHAR(128) NOT NULL COMMENT '名称',
    display_name    VARCHAR(128) DEFAULT '' COMMENT '显示名称',
    description     VARCHAR(500) DEFAULT '' COMMENT '描述',
    category        VARCHAR(32)  DEFAULT 'custom' COMMENT '分类: nl2sql/analysis/chart/report/custom',
    system_prompt   TEXT         COMMENT '基础系统提示词(角色设定)',
    persona         JSON         COMMENT '职责/风格/边界: {"responsibility","style","boundary"}',
    tools           JSON         COMMENT '允许的工具组与标准工具: {"groups":["catalog","query","semantic"],"standard":["read","grep",...]}',
    mcp_server_ids  JSON         COMMENT '引用的共享 MCP 服务 ID 列表',
    datasource_ids  JSON         COMMENT '引用的共享数据源 ID 列表',
    skills          JSON         COMMENT '内联技能片段: [{"key","name","instruction"}]',
    models          JSON         COMMENT 'Chat 端可选模型列表(model_ref 字符串数组;留空=用执行层默认模型)',
    chart_enabled   TINYINT      DEFAULT 1 COMMENT '是否注入图表输出契约',
    permission_mode VARCHAR(32)  DEFAULT 'inherit' COMMENT '权限模式: inherit/restrict(继承用户角色权限)',
    is_active       TINYINT      DEFAULT 1,
    is_builtin      TINYINT      DEFAULT 0 COMMENT '内置 Waker 不可删除',
    workspace_id    BIGINT       DEFAULT 0 COMMENT '0=全局 Waker',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_by      VARCHAR(64)  DEFAULT 'admin',
    PRIMARY KEY (id),
    UNIQUE KEY uk_waker_key (waker_key),
    INDEX idx_workspace (workspace_id),
    INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Waker 角色化智能体定义';

-- ============================================================================
-- 2. 工作空间-Waker 绑定
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_workspace_wakers (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    workspace_id    BIGINT NOT NULL,
    waker_id        BIGINT NOT NULL,
    is_default      TINYINT      DEFAULT 0 COMMENT '工作空间默认 Waker',
    sort            INT          DEFAULT 0 COMMENT '展示顺序',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_ws_waker (workspace_id, waker_id),
    INDEX idx_workspace (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工作空间可用 Waker 授权';

-- ============================================================================
-- 3. 角色-Waker 绑定
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_role_wakers (
    id              BIGINT NOT NULL AUTO_INCREMENT,
    role_id         BIGINT NOT NULL COMMENT '角色 ID(adh_roles.id)',
    waker_id        BIGINT NOT NULL,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_role_waker (role_id, waker_id),
    INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色可用 Waker 授权';

-- ============================================================================
-- 4. 内置 Waker 种子(workspace_id=0 全局可用)
-- ============================================================================
INSERT IGNORE INTO adh_wakers
    (waker_key, name, display_name, description, category, system_prompt, persona, tools, chart_enabled, is_active, is_builtin, workspace_id)
VALUES
    ('data_analyst', 'data_analyst', '数据分析师', '面向业务分析场景,自主取数、分析并输出图表结论', 'analysis',
     '你是企业数据分析师,负责根据用户问题自主规划、查询数据并给出分析结论。',
     '{"responsibility":"理解业务问题,规划取数与分析路径,输出可行动的洞察","style":"专业、简洁、数据驱动,中文回答","boundary":"只回答数据相关问题,不臆造数据,涉及权限外数据应说明并拒绝"}',
     '{"groups":["catalog","query","semantic"],"standard":["read","grep","glob"]}',
     1, 1, 1, 0),
    ('nl2sql_expert', 'nl2sql_expert', 'SQL 取数专家', '专注自然语言转 SQL,精确生成并执行查询', 'nl2sql',
     '你是 NL2SQL 专家,将自然语言问题转换为准确、高效的 SQL 并执行。',
     '{"responsibility":"检索表结构元数据,生成正确的 SQL,执行并解读结果","style":"严谨,展示 SQL 与结果,中文说明","boundary":"仅执行只读查询,禁止破坏性 SQL"}',
     '{"groups":["catalog","query"],"standard":["read"]}',
     1, 1, 1, 0);

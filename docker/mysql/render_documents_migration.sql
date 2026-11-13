-- ============================================================================
-- 渲染机文档对象表（Chat 交付形态：chart / html / spec 三渲染机）
--
-- 背景：复杂分析场景原先只能得到一串离散 ```chart 卡片（正文与图割裂、无整体可
--   读交付物），而"整份 HTML 报告"只能作为会话工作区文件交付 —— 判定面/存储面/
--   渲染面三侧口径不一致（后端只认 artifact 的 path、正文不进消息、报告中心拿
--   不到内联正文）。本表把"报告/图表正文"升格为**受管文档对象**：正文外置在库，
--   消息内只留引用块（对用户它是对话里直接展开的报告，不是去文件清单找下载）。
-- 设计要点：
--   · 主键 uuid hex（对齐 adh_as_bot_dashboard_designs 的引用式 id 惯例）：
--     渲染机回执给模型的是这个 id，模型后续只能引用它、不能猜任何内部标识；
--   · message_uuid 只作审计与回放定位，**不参与唯一约束**（观测未开启时它为空串，
--     当唯一键会让同会话第二份报告撞主键，宁可用不可靠的列当索引也不要当身份）；
--   · body 不进消息 content：conversation_store 的容量闸门不覆盖 content，
--     整份 HTML 落消息会把会话存储结构改掉（历史统计正文仅占 3.6%）。
-- 约定：不含 USE，用 `mysql ... <METADATA_DB_DATABASE> < 本文件` 施加；
--       CREATE TABLE IF NOT EXISTS 天然幂等，可重复执行。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_render_documents (
    id              CHAR(32)     NOT NULL PRIMARY KEY COMMENT '文档对象 id（渲染机回执给模型与前端的唯一引用）',
    conversation_id BIGINT       NOT NULL COMMENT '归属会话（会话生命周期内可见）',
    message_uuid    CHAR(32)     NULL COMMENT '产出该文档的回合消息 id（仅审计/回放定位，可空）',
    user_id         BIGINT       NOT NULL COMMENT '属主（读取时唯一校验归属，不信任入参）',
    workspace_id    BIGINT       NOT NULL DEFAULT 0 COMMENT '工作空间归属（仅管理归属，不作可见性判断）',
    kind            VARCHAR(16)  NOT NULL DEFAULT 'doc' COMMENT '对象类型: doc=整体文档, chart=单图数据对象',
    body_kind       VARCHAR(16)  NOT NULL COMMENT '正文形态: html=自包含文档, spec=结构化章节, chart-data=图表数据',
    title           VARCHAR(255) NOT NULL DEFAULT '' COMMENT '报告/图表标题（展示用业务名）',
    theme           VARCHAR(64)  NULL COMMENT '生成时采用的主题 id（html 为快照，spec 随查看者即时换装）',
    template_key    VARCHAR(64)  NULL COMMENT '套用的报告模板业务名（版式契约来源，空=未用模板）',
    body            LONGTEXT     NOT NULL COMMENT '文档正文（html 原文 / spec JSON / chart 数据 JSON）',
    digest          CHAR(32)     NOT NULL DEFAULT '' COMMENT '正文 MD5（预览失效判定与"改了什么"可诊断）',
    size_chars      INT          NOT NULL DEFAULT 0 COMMENT '正文字符数（超限判定与体积记账）',
    warnings        JSON         NULL COMMENT '体检/硬门的非阻断提示（对用户可见，不得静默）',
    row_counts      JSON         NULL COMMENT '服务端取数对账结果（渲染机不自取时为 NULL）',
    created_at      DATETIME(6)  NOT NULL,
    updated_at      DATETIME(6)  NOT NULL,
    INDEX idx_render_doc_conversation (conversation_id, kind),
    INDEX idx_render_doc_owner (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Chat 渲染机产出的受管文档对象（正文外置、消息内引用）';

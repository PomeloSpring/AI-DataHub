-- ============================================================================
-- 会话消息拆行存储（P1）—— 新建 adh_conversation_messages + 存量回填
-- ============================================================================
-- 动因（2026-11 存储审计）：adh_conversations.messages 单列 LONGTEXT 装整份 JSON 数组，
-- 每轮 PUT 全列覆盖造成 O(n²) 写放大（ROW binlog 还要落 before/after 两份镜像），
-- 且把工具原始返回体/思考链一并明文常驻。现改为一条消息一行 + 增量 diff 写入。
--
-- 事实源代码：backend/modules/mind/services/conversation_store.py
--
-- 应用方式（幂等，可重复执行；本脚本不含 USE，按库名施加）：
--   mysql -h HOST -P PORT -u USER -p DATABASE < docker/mysql/conversation_messages_split_migration.sql
--   或 venv/bin/python logs/apply_migration.py docker/mysql/conversation_messages_split_migration.sql
--
-- 遗留列处置：adh_conversations.messages **不删不改**，退役为迁移证据列，
-- 业务读写路径一律不得消费（tests/test_conversation_store.py 有源码级护栏）。
-- 是否 DROP 由人工在核验后决策，不在本脚本擅自删数据。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_conversation_messages (
    id              BIGINT      NOT NULL AUTO_INCREMENT,
    conversation_id BIGINT      NOT NULL COMMENT '所属会话 adh_conversations.id',
    user_id         BIGINT      NOT NULL DEFAULT 0 COMMENT '属主（服务端上下文写入，不从请求体取）',
    workspace_id    BIGINT      NOT NULL DEFAULT 0 COMMENT '归属工作空间（工作空间删除级联用）',
    seq             INT         NOT NULL COMMENT '会话内序号，从 0 开始，与历史数组下标一致',
    role            VARCHAR(16) NOT NULL DEFAULT '' COMMENT 'user / assistant / 其他',
    content         MEDIUMTEXT  NULL COMMENT '用户可见正文（不加密；敏感列在取数时已按策略改写）',
    meta            MEDIUMTEXT  NULL COMMENT '除 role/content 外的展示字段 JSON（已按入库投影裁剪）',
    message_uuid    VARCHAR(64) NOT NULL DEFAULT '' COMMENT '回合标识，服务端兜底落库判重用',
    digest          CHAR(32)    NOT NULL DEFAULT '' COMMENT '投影后消息 md5，增量改写判等；空=迁移回填未算，首次改写自愈',
    storage_state   VARCHAR(16) NOT NULL DEFAULT 'full' COMMENT 'full | trimmed（保留策略裁剪后为 trimmed）',
    created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_conv_seq (conversation_id, seq),
    KEY idx_conv (conversation_id),
    KEY idx_user_conv (user_id, conversation_id),
    KEY idx_workspace (workspace_id),
    KEY idx_uuid (conversation_id, message_uuid),
    KEY idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话消息拆行表（一条消息一行，取代 messages 整列覆盖写）';

-- ----------------------------------------------------------------------------
-- 存量回填：把 messages JSON 数组逐条展开进拆行表
-- ----------------------------------------------------------------------------
-- 纪律：
--   * 只处理"尚无拆行"的会话（NOT EXISTS 守卫）—— 重复执行不产生第二份；
--   * JSON_VALID 不成立的行**不猜不修**，留到最后一条诊断 SELECT 显式列出，人工处置；
--   * 原列内容不动，便于回滚核对；
--   * digest 留空（回填不算 md5），下一次改写自然补齐 —— 不做双口径判断。
-- 写法要点：JSON_TABLE 的 FOR ORDINALITY 列**不能写类型**（`col FOR ORDINALITY` 是唯一
-- 合法形式），且不能写表达式，故在外层做 ord - 1 换算成 0 基 seq；
-- meta 用 JSON_REMOVE 剥掉 role/content 两键（它们已是独立列，重复存会二次膨胀）。
INSERT INTO adh_conversation_messages
    (conversation_id, user_id, workspace_id, seq, role, content, meta, message_uuid, digest, storage_state)
SELECT c.id,
       c.user_id,
       COALESCE(c.workspace_id, 0),
       jt.ord - 1,
       COALESCE(jt.role, ''),
       COALESCE(jt.content, ''),
       CAST(JSON_REMOVE(jt.msg, '$.role', '$.content') AS CHAR CHARACTER SET utf8mb4),
       COALESCE(jt.message_uuid, ''),
       '',
       'full'
FROM adh_conversations c, JSON_TABLE(
        c.messages,
        '$[*]' COLUMNS (
            ord                    FOR ORDINALITY,
            msg          JSON        PATH '$',
            role         VARCHAR(16) PATH '$.role',
            content      MEDIUMTEXT  PATH '$.content',
            message_uuid VARCHAR(64) PATH '$.message_uuid'
        )) AS jt
WHERE c.messages IS NOT NULL
  AND JSON_VALID(c.messages)
  AND JSON_LENGTH(c.messages) > 0
  AND NOT EXISTS (SELECT 1 FROM adh_conversation_messages m WHERE m.conversation_id = c.id);

-- ----------------------------------------------------------------------------
-- 回填核验（执行后人工核对；两条都必须返回 0 行才算迁移完成）
-- ----------------------------------------------------------------------------
-- ① 有历史消息却没能回填上的会话（JSON 非法 / 空数组 / 结构异常）—— 必须显式暴露
SELECT c.id AS conversation_id, c.user_id,
       ROUND(LENGTH(c.messages) / 1024, 1) AS legacy_kb,
       JSON_VALID(c.messages) AS json_valid
FROM adh_conversations c
WHERE COALESCE(JSON_LENGTH(CASE WHEN JSON_VALID(c.messages) THEN c.messages ELSE '[]' END), 0) > 0
  AND NOT EXISTS (SELECT 1 FROM adh_conversation_messages m WHERE m.conversation_id = c.id);

-- ② 条数不一致的会话（回填漏项/多写）—— 逐会话比对
SELECT c.id AS conversation_id,
       JSON_LENGTH(c.messages) AS legacy_count,
       (SELECT COUNT(*) FROM adh_conversation_messages m WHERE m.conversation_id = c.id) AS split_count
FROM adh_conversations c
WHERE c.messages IS NOT NULL AND JSON_VALID(c.messages) AND JSON_LENGTH(c.messages) > 0
  AND JSON_LENGTH(c.messages) <> (SELECT COUNT(*) FROM adh_conversation_messages m
                                  WHERE m.conversation_id = c.id);

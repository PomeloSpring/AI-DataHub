-- ============================================================================
-- 前置意图漏斗（Intent Gate）迁移 (幂等, information_schema 守卫, 可重复执行)
--
-- 背景: chat 的任何输入原先都无条件进 qodercli 执行层，每回合都要付一次完整
-- agent 回合的成本（长 system_prompt + 全部 MCP 工具 schema + resume 历史 +
-- 工具循环）。寒暄/无效输入/能力询问这类不需要模型的输入也照付。
-- 现在在 `ChatService._try_dispatch_via_execution_layer` 之前加一道确定性分流
-- （见 backend/modules/mind/execution/intent_gate.py），本文件补它需要的两处 schema：
--
--   1) adh_llm_traces.handled_by —— 现有 LLM 可观测里新增"这一条是谁答的"类别：
--        agent       = 走了执行层（模型）
--        intent_gate = 前置意图层本地直答（未调用模型，零 credit）
--      缺省 'agent' 保证存量行与未接线的入口（playground/scheduled/embed）语义不变。
--
--   2) adh_intent_intercepts —— 判定全量落库（不只是被拦的那些）。
--      为什么可观测列不够：OBSERVABILITY_ENABLED=false 时 trace 整条不落库，
--      而"拦截率/误拦复盘"是切 enforce 的唯一依据，必须有独立于观测的记录。
--      decision 三态把"判定结论"与"实际行为"分开，shadow 期不被混淆：
--        local         = 判定可拦且已本地直答（enforce）
--        shadow_local  = 判定可拦但行为未变（shadow，仍由 agent 回答）
--        agent         = 判定必须交模型
--      gate_error 非空 = 网关自己坏了/超预算所以放行 —— 这类行必须数得出来，
--      否则"全都放行"看起来像"全都正常"（no-silent-degradation）。
--
-- 保留期: shadow 期每轮都落一行，量级 = chat 输入量。清理交由
-- backend/scripts（会话清理同范式）按 90 天滚动删除，不在本表上做逻辑删除。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) adh_llm_traces: 新增处理方式列 + 时间维度索引（按处理方式筛选/聚合）
-- ----------------------------------------------------------------------------
SET @has_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE table_schema = DATABASE() AND table_name = 'adh_llm_traces'
                   AND column_name = 'handled_by');
SET @ddl := IF(@has_col = 0,
  'ALTER TABLE adh_llm_traces ADD COLUMN handled_by VARCHAR(16) NOT NULL DEFAULT ''agent'' COMMENT ''本回合处理方: agent=执行层模型 / intent_gate=前置意图层本地直答'' AFTER entrypoint',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @has_idx := (SELECT COUNT(DISTINCT INDEX_NAME) FROM information_schema.STATISTICS
                 WHERE table_schema = DATABASE() AND table_name = 'adh_llm_traces'
                   AND INDEX_NAME = 'idx_handled_time');
SET @ddl := IF(@has_idx = 0,
  'ALTER TABLE adh_llm_traces ADD INDEX idx_handled_time (handled_by, started_at)',
  'SELECT 1');
PREPARE stmt2 FROM @ddl; EXECUTE stmt2; DEALLOCATE PREPARE stmt2;

-- ----------------------------------------------------------------------------
-- 2) adh_intent_intercepts: 意图漏斗判定流水
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_intent_intercepts (
  id              BIGINT NOT NULL AUTO_INCREMENT,
  dt              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '判定时间(清理与趋势聚合按此列)',
  trace_id        VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '关联 adh_llm_traces(观测未开启时为空)',
  conversation_id BIGINT       NOT NULL DEFAULT 0,
  user_id         BIGINT       NOT NULL DEFAULT 0,
  workspace_id    BIGINT       NOT NULL DEFAULT 0,
  datasource_id   BIGINT       NOT NULL DEFAULT 0 COMMENT '会话生效源(内部 id, 只用于服务端归因, 不外泄)',
  as_bot_key      VARCHAR(64)  NOT NULL DEFAULT '',
  mode            VARCHAR(16)  NOT NULL DEFAULT '' COMMENT 'off/shadow/enforce',
  layer           VARCHAR(8)   NOT NULL DEFAULT '' COMMENT 'L0=形态规则 L1=词典/本体命中 agent=直接放行',
  label           VARCHAR(32)  NOT NULL DEFAULT '' COMMENT 'empty_or_noise/social_ack/probe_refuse/help_usage/capability_probe/data_query/platform_write/context_followup/ambiguous',
  decision        VARCHAR(16)  NOT NULL DEFAULT '' COMMENT 'local/shadow_local/agent',
  reason          VARCHAR(500) NOT NULL DEFAULT '' COMMENT '人类可读判定依据(误拦复盘靠它, 不靠猜)',
  signals         TEXT         NULL COMMENT '命中信号 JSON(entity_hit:*/ack_only/secret_probe/...)',
  question_digest VARCHAR(500) NOT NULL DEFAULT '' COMMENT '输入摘要(截断 500, 不落附件内容)',
  latency_ms      INT          NOT NULL DEFAULT 0 COMMENT '判定耗时(超预算即放行并记 gate_error)',
  gate_error      VARCHAR(300) NOT NULL DEFAULT '' COMMENT '网关自身异常/超预算事实, 空=判定链路健康',
  force_agent     TINYINT      NOT NULL DEFAULT 0 COMMENT '1=用户点「转深度分析」显式绕过漏斗',
  reviewed        TINYINT      NOT NULL DEFAULT 0 COMMENT '误拦复盘标注: 已人工确认判定是否站得住',
  review_note     VARCHAR(500) NOT NULL DEFAULT '',
  PRIMARY KEY (id),
  INDEX idx_dt_decision (dt, decision),
  INDEX idx_dt_label (dt, label),
  INDEX idx_conv (conversation_id),
  INDEX idx_trace (trace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='前置意图漏斗判定流水（含放行记录；拦截率/误拦复盘与 enforce 决策的数据源）';

-- 存量表补列（早期版本无复盘两列时补齐；重复执行安全）
SET @has_reviewed := (SELECT COUNT(*) FROM information_schema.COLUMNS
                      WHERE table_schema = DATABASE() AND table_name = 'adh_intent_intercepts'
                        AND column_name = 'reviewed');
SET @ddl := IF(@has_reviewed = 0,
  'ALTER TABLE adh_intent_intercepts ADD COLUMN reviewed TINYINT NOT NULL DEFAULT 0 COMMENT ''误拦复盘标注'' AFTER force_agent',
  'SELECT 1');
PREPARE stmt3 FROM @ddl; EXECUTE stmt3; DEALLOCATE PREPARE stmt3;

SET @has_review_note := (SELECT COUNT(*) FROM information_schema.COLUMNS
                         WHERE table_schema = DATABASE() AND table_name = 'adh_intent_intercepts'
                           AND column_name = 'review_note');
SET @ddl := IF(@has_review_note = 0,
  'ALTER TABLE adh_intent_intercepts ADD COLUMN review_note VARCHAR(500) NOT NULL DEFAULT '''' COMMENT ''复盘备注'' AFTER reviewed',
  'SELECT 1');
PREPARE stmt4 FROM @ddl; EXECUTE stmt4; DEALLOCATE PREPARE stmt4;

-- ----------------------------------------------------------------------------
-- 3) 评测门禁策略: intent 集（前置分流）绝对下限
--    分流错误的代价是"用户拿不到答案"，所以 floor 必须拉满 1.0；
--    min_cases=10 拦住"删难用例刷通过率"（把用例改掉来让门禁变绿不是修复）。
--    列名以现表为准：enabled（无 is_active/note），uk_eval_gate(suite,capability) 上幂等。
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO adh_eval_gate_policy (suite, capability, floor_accuracy, min_cases, enabled)
VALUES ('intent', 'dispatch', 1.0000, 10, 1);

-- ============================================================================
-- 知识更新提议：Chat / 人工 → 进化中心待审 → 人用目标域权限落库 (幂等, 无 USE)
--
-- 为什么需要它（不是"又一张待办表"）
-- ----
-- 用户拍板的进化闭环包含三类知识更新：**本体对象标量/别名**、**知识库文档正文**、
-- **分析场景的新建与追加**；且要求 Chat 里能"顺手提交知识更新"。
-- 候选表 adh_evolution_candidates 装不下这类提议：
--   * 它的唯一键是 (term, datasource_name, kind)，同一篇文档的多次提议会撞成一行，
--     后一次把前一次的内容顶掉（正文是不可合并的）；
--   * 它的 term 只 128 字符、证据塞 evidence JSON，装不下 Markdown 正文。
-- 所以另立本表：一条提议 = 一份**待确认的备忘录**，不直接改任何资产。
--
-- 关键口径
-- --------
-- * 提议只是备忘录：状态 applied 由前端在**目标域写入成功后**回写（写知识库走
--   kbdoc:write、写场景走 knowledge:manage、写本体走 ontology:save/publish）。
--   AI 不直写知识库（kbdoc:write 的 ai_access=none），也不越权改本体。
-- * 目标用**业务名**表达（target_hint：知识库名/文档标题/场景标题/对象业务名），
--   不存任何内部 id —— LLM 拿不到 id 只能猜（security-guardrails §7）。
--   具体落到哪一行由人在界面用选择框确认（ui-resource-display §2）。
-- * source=chat 的提议落 origin_conversation_id / origin_message_uuid，便于回看
--   是哪轮对话让它进队的（与候选表同口径）。
-- * knowledge:manage 的 ai_action_key 必须指向代码里真实存在的功能动作
--   knowledge_proposal.create（function_tools.FUNCTION_ACTION_SPECS），
--   否则角色页会显示一个"配了没用"的 AI 通道（tests/test_function_ai_access.py 把门）。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_knowledge_update_proposals (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  source ENUM('chat','manual','evolution') NOT NULL DEFAULT 'manual'
         COMMENT 'chat=对话里由 LLM 提议，manual=人在进化中心手填，evolution=由缺口候选派生',
  target_type ENUM('kb_doc','scenario','ontology_object') NOT NULL
              COMMENT '落点域：知识库文档正文 / 分析场景 / 本体对象标量与别名',
  title VARCHAR(191) NOT NULL DEFAULT ''
        COMMENT '提议标题（文档标题/场景标题/对象业务名；人可改）',
  content MEDIUMTEXT COMMENT '提议正文（Markdown / 场景正文片段 / 对象描述），人确认时可编辑',
  rationale VARCHAR(1000) NOT NULL DEFAULT ''
            COMMENT '为什么提这条（用户纠正了什么/口径来自哪），用于人工判断',
  target_hint VARCHAR(191) NOT NULL DEFAULT ''
              COMMENT '目标业务名提示（知识库名/文档标题/场景标题/对象业务名），不存内部 id',
  datasource_name VARCHAR(128) NOT NULL DEFAULT ''
                  COMMENT '所属数据源业务名（本体对象类提议用；其余可空）',
  status ENUM('pending','applied','rejected','superseded') NOT NULL DEFAULT 'pending'
         COMMENT 'applied=人已在目标域落库并回写，rejected=人判定不收，superseded=被后一条同目标提议取代',
  applied_ref VARCHAR(191) NOT NULL DEFAULT ''
             COMMENT '落点标识：kb_name/source_id、场景 id、对象 key，便于回看改到哪了',
  decided_by VARCHAR(64) NOT NULL DEFAULT '' COMMENT '处理人（服务端注入，不信任请求体）',
  decided_at DATETIME NULL,
  decided_note VARCHAR(500) NOT NULL DEFAULT '' COMMENT '驳回/取代原因（人写的理由，便于回看为什么没收）',
  origin_conversation_id BIGINT NOT NULL DEFAULT 0
                         COMMENT '首次提交的对话会话 id（0=非对话来源）',
  origin_message_uuid VARCHAR(64) NOT NULL DEFAULT '',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_kup_queue (status, target_type, created_at),
  INDEX idx_kup_origin (origin_conversation_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='知识更新提议（备忘录；落库由人在目标域执行）';

-- AI 通道镜像：功能动作 knowledge_proposal.create ↔ 权限码 knowledge:manage
UPDATE adh_perm_registry
   SET ai_action_key='knowledge_proposal.create',
       ai_note='提议知识更新（只入进化中心待审队列，不直写本体/知识库/场景）'
 WHERE perm_code='knowledge:manage';

-- 幂等加列：decided_note（本表首版未含，补列用 information_schema 判定，可重复执行）
SET @kup_add_note = (SELECT IF(
  EXISTS(SELECT 1 FROM information_schema.columns
         WHERE table_schema=DATABASE() AND table_name='adh_knowledge_update_proposals'
           AND column_name='decided_note'),
  'SELECT 1',
  'ALTER TABLE adh_knowledge_update_proposals ADD COLUMN decided_note VARCHAR(500) NOT NULL DEFAULT '''' COMMENT ''驳回/取代原因（人写的理由，便于回看为什么没收）'''));
PREPARE kup_stmt FROM @kup_add_note;
EXECUTE kup_stmt;
DEALLOCATE PREPARE kup_stmt;

-- 核验（人工执行）：
-- SELECT perm_code, ai_access, ai_action_key, ai_note FROM adh_perm_registry
--   WHERE perm_code='knowledge:manage';
-- SHOW COLUMNS FROM adh_knowledge_update_proposals;
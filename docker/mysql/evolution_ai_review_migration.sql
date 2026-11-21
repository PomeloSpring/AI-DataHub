-- ============================================================================
-- 进化中心审批接入 Chat（用户 2026-11-21 拍板「双闸」）
--
-- 为什么改：进化中心的处置（收编别名/转术语/驳回候选/建指标维度草稿/处置知识更新提议/
-- 立即归纳）此前只有**人**能在页面点：`evolution:read|manage` 的 ai_access 都是 none，
-- 于是 Chat 里即使角色有权限也只能说“不开放给 AI”。现在允许 AI 在同一把权限尺下处置，
-- 但不是放开一个总开关 —— 采用**双闸**：
--   闸一 能力面：`evolution:read`（看待办）/ `evolution:manage`（执行处置）
--        —— 本迁移把 ai_access 由 none 抬到 read/write，并镜像 ai_action_key；
--   闸二 动作面：每个出口仍按它**本来所属域**的权限码裁决，与 HTTP 端点同一把尺：
--        收编别名/转术语/驳回候选 → ontology:save
--        建指标/维度草稿          → metrics:manage
--        知识更新提议 标记/驳回    → knowledge:manage
--        立即执行归纳             → evolution:manage
--   两道闸都过才执行（perm_link.require_write_perm 在工具 handler 内再裁一次，fail-closed）。
--
-- 为什么不改 eval:*：评价 AI 的标尺（评测用例/门禁）不能由 AI 自己读写，
-- `eval:*` 的 ai_access 保持 none（候选转评测用例仍只能由人操作）。
--
-- 标识口径：工具入参只收业务名（候选 term / 对象业务名 / 属性业务名 / 提议标题 /
-- 数据源业务名），内部 id 与物理列名既不下发也不接受（护栏 §7）。decided_by 由服务端注入。
--
-- 幂等：纯 UPDATE，可重复执行；不写 `USE <db>;`（库名只由连接配置决定，script-hygiene §2）。
-- ============================================================================

-- ── 1. 读面：能看审批队列（只读通道，不产生任何写入）───────────────────────
UPDATE adh_perm_registry
SET ai_access = 'read',
    ai_action_key = 'evolution_queue.list',
    ai_note = '只读：按角色可见进化中心待处置候选与待审提议（不含内部 id）。处置动作另由 evolution:manage 及各域码裁决'
WHERE perm_code = 'evolution:read';

-- ── 2. 处置面：能执行审批动作（能力面；动作面仍按各出口所属域码再裁一次）────
-- ai_action_key 长度 205（< 255），顺序与 function_tools.FUNCTION_ACTION_SPECS 一致。
UPDATE adh_perm_registry
SET ai_access = 'write',
    ai_action_key = 'evolution_candidate.adopt,evolution_candidate.to_term,evolution_candidate.reject,evolution_metric.create,evolution_dimension.create,evolution_proposal.applied,evolution_proposal.reject,evolution_digest.run',
    ai_note = '双闸第一道（能否进处置面）：动作本身仍按 ontology:save/metrics:manage/knowledge:manage 裁决；eval:* 不接 AI'
WHERE perm_code = 'evolution:manage';

-- ── 3. 动作面的域码：必须是 write，否则双闸的第二道永远不开 ─────────────────
-- 只纠“被配成 none/read”的历史行；已是 write 的不动（幂等）。
UPDATE adh_perm_registry
SET ai_access = 'write'
WHERE perm_code IN ('ontology:save', 'metrics:manage', 'knowledge:manage')
  AND ai_access <> 'write';

-- ----------------------------------------------------------------------------
-- 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- 权限点级别与镜像：
-- SELECT perm_code, ai_access, ai_action_key, ai_note FROM adh_perm_registry
--   WHERE perm_code IN ('evolution:read','evolution:manage','ontology:save',
--                       'metrics:manage','knowledge:manage','eval:manage');
-- 预期：evolution:read=read、evolution:manage=write（ai_action_key 9 个动作、
--       evolution:read 1 个）；eval:manage 仍 none。
-- 双侧一致（DB 镜像 == FUNCTION_ACTION_SPECS）由用例把门：
--   venv/bin/python -m pytest tests/test_function_ai_access.py -q
-- 角色反查：admin 持有 evolution:read/manage + ontology:save + metrics:manage + knowledge:manage
-- SELECT DISTINCT p.perm_code FROM adh_role_perms rp
--   JOIN adh_perm_registry p ON p.perm_code = rp.perm_code
--  WHERE rp.role_id = 1 ORDER BY p.perm_code;
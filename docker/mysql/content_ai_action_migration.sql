-- ============================================================================
-- 内容中心 → LLM 功能能力接入（在 Chat 里用技能生成内容 / 管理内容）
--
-- 需求（用户 2026-11-05）：内容中心创建内容要做成一个技能给 LLM 使用，可以在 chat
-- 页面用技能生成内容和管理内容，**权限由角色权限管理**。
--
-- 落点（不新建第二套判断，全部走既有 A 维度：角色权限码 → ai_access → 功能动作）：
--   * 动作实现与登记在 `function_tools.FUNCTION_ACTION_SPECS`（6 个 content_* 动作）；
--   * 本迁移负责**双侧对齐**：把动作 key 回填到 `adh_perm_registry.ai_action_key`，
--     并由 `tests/test_function_ai_access.py::test_registry_matches_db_ai_action_keys`
--     锁住"注册表有 DB 无 / DB 有注册表无"都必须为 0；
--   * 授权来源仍是 `article:manage`：谁的角色勾了它，谁的 AS-BOT 才拿得到这 6 个工具。
--
-- 三条边界（都是刻意配置，不是漏做）：
--   1. **发布 / 下架 / 审核不开放 AI**：`article:publish` 与 `article:review` 的
--      `ai_access=none` 保持不变。对外内容代表公司立场，必须有人负责；AI 只能写草稿
--      并提交审核，最后一步由人点。
--   2. **同步知识库语料不开放 AI**：`kbdoc:write` 的 `ai_access=none` 保持不变。
--      改语料等于改 AI 自己的知识依据，只允许人做。
--   3. **不给非 admin 角色扩权**：只给 admin 角色补 `article:manage`（下面 §3 说明为什么
--      必须补这一行），其余角色由管理员在「角色权限」里显式勾选。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1) ai_action_key 列宽：VARCHAR(64) 装不下 6 个动作名（拼起来 126 字符）
--    非严格模式下 MySQL 会**静默截断**，截断 = 一半动作没登记 = 功能悄悄少一批
--    （正是 no-silent-degradation 禁止的形态），所以先显式扩到 255。
--    与 report:manage 挂两个动作（report.generate,report.save）是同一口径，只是量更大。
-- ----------------------------------------------------------------------------
SET @len_now = (
    SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_perm_registry'
      AND COLUMN_NAME = 'ai_action_key'
);
SET @ddl = IF(@len_now < 255,
    'ALTER TABLE adh_perm_registry MODIFY COLUMN ai_action_key VARCHAR(255) NOT NULL DEFAULT '''' COMMENT ''登记即生成 LLM 功能工具(function_tools.FUNCTION_ACTION_SPECS 的 action_key, 逗号分隔可挂多个), 空=不接 AI''',
    'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 2) article:manage 挂上 6 个内容动作（幂等：整串重写，重跑结果一致）
--    ai_access 仍是 write（上一批内容中心迁移已配好），这里只补 ai_action_key 与原因文字。
-- ----------------------------------------------------------------------------
UPDATE adh_perm_registry
SET ai_action_key = 'content_article.list,content_article.get,content_taxonomy.list,content_draft.create,content_draft.update,content_review.submit',
    ai_note = 'AI 可列内容/读正文/读栏目标签名册/写草稿/改草稿/提交审核（改动只落工作稿，发布不在此列）。发布与下架归 article:publish、审核归 article:review，两者 ai_access=none 不开放 AI'
WHERE perm_code = 'article:manage';

-- 明确把"审核/发布/语料写不给 AI"这件事写进注册表（前端与角色页要显示原因，
-- 不能只存在于代码注释里 —— 否则管理员把 ai_access 改成都以为能让 AI 发布）
UPDATE adh_perm_registry
SET ai_note = '审核是人的判断，不得由 AI 自审自批（与「评测用例不得自评自证」同构）。放开需显式改配置'
WHERE perm_code = 'article:review';
UPDATE adh_perm_registry
SET ai_note = '对外内容代表公司立场，必须有人负责：发布/下架不开放 AI 调用（AI 只能写草稿并提交审核）。免审直发需本码 + 必填原因入审计'
WHERE perm_code = 'article:publish';
UPDATE adh_perm_registry
SET ai_note = '语料是 AS-BOT 的回答依据，改它等于改 AI 的知识：只允许人做，不开放 AI 调用。每次写入落审计'
WHERE perm_code = 'kbdoc:write';

-- ----------------------------------------------------------------------------
-- 3) 给 admin 角色补 article:manage（这一行不补，Chat 里谁都调不到内容工具）
--    根因口径：**功能能力继承只读 DB 权限码**（role_service.get_user_role_ai_perms
--    → adh_user_roles ⋈ adh_role_perms ⋈ adh_perm_registry.ai_access），
--    它不看 JWT 的 user_role，也不吃 API 中间件那条 admin unrestricted 旁路。
--    上一批内容中心迁移刻意"存量角色一个都不授"（避免给所有角色扩写权限），
--    于是 admin 角色的 ai_perms 里也没有 article:manage → 6 个工具对任何 AS-BOT
--    都不会注册。这里只补 admin 角色（它本来就能进内容管理页），其余角色仍由管理员显式勾。
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO adh_role_perms (role_id, perm_code)
SELECT r.id, 'article:manage'
FROM adh_roles r
WHERE r.name = 'admin';

-- ----------------------------------------------------------------------------
-- 4) 核验（人工执行确认，双侧一致另有 pytest 锁）
-- ----------------------------------------------------------------------------
-- 列宽与回填值：
-- SELECT CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS
--   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_perm_registry'
--     AND COLUMN_NAME='ai_action_key';
-- SELECT perm_code, ai_access, ai_action_key, ai_note FROM adh_perm_registry
--   WHERE perm_code LIKE 'article:%' OR perm_code LIKE 'kbdoc:%' ORDER BY sort;
-- admin 角色已拿到编辑内容权（AI 通道的入口条件）：
-- SELECT r.name, rp.perm_code FROM adh_roles r JOIN adh_role_perms rp ON rp.role_id=r.id
--   WHERE rp.perm_code='article:manage';
-- 其余角色仍为空 = 预期（由管理员在「角色权限 → 菜单与功能」勾选后才可用）：
-- SELECT DISTINCT m.menu_key, r.name FROM adh_menu_registry m
--   JOIN adh_role_perms rp ON rp.perm_code=m.perm_code
--   JOIN adh_roles r ON r.id=rp.role_id WHERE m.menu_key LIKE 'content:%';

-- ----------------------------------------------------------------------------
-- 5) 把「内容创作」技能绑到 admin AS-BOT（Chat 里 / 面板能点到）
--    技能正文本身是文件事实源（backend/modules/mind/config/skills/content-writer/SKILL.md），
--    AS-BOT 只存"绑定了哪些技能名"。这里做 JSON 追加而不是整列重写：
--    管理员可能已在 AS-BOT 配置页勾过别的技能，覆盖写会把别人的选择抹掉。
--    注意：`as_bot_migration.sql` 是整列硬赋值（重跑它会重置技能清单），
--    若重跑那条，需要再跑一次本 §5。
--    生效窗口：AS-BOT 配置有 60s TTL 缓存（config_cache），直连 DB 写入不走失效钩子，
--    最多等一分钟；从 AS-BOT 配置页保存则当场失效。
-- ----------------------------------------------------------------------------
UPDATE adh_as_bots
SET skills = JSON_ARRAY_APPEND(COALESCE(skills, JSON_ARRAY()), '$', 'content-writer')
WHERE as_bot_key = 'admin'
  AND NOT JSON_CONTAINS(COALESCE(skills, JSON_ARRAY()), '"content-writer"', '$');

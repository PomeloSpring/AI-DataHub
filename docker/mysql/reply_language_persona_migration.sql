-- 回复语言口径收敛：角色 persona 不再写死「中文回答」（2026-11-16）
--
-- 背景：前端已有 zh-CN / en-US 语言切换，但语言从没传到后端，persona 与技能正文里到处
-- 硬编码「中文回答」——用户切到 English 后界面是英文、LLM 仍回中文（配置面有、消费端没有的空承诺）。
-- 现在回复语言唯一由系统提示词的「回复语言」段决定（判定在 backend/common/i18n.resolve_reply_language：
-- 提问语言优先 → 界面语言兜底 → 默认中文），persona 里的语言字样必须一并抹掉，否则两处口径互相矛盾，
-- 模型会听 persona 的那句（persona 更靠近"你是谁"，压过通用输出契约）。
--
-- 幂等：只在命中时改写，重跑影响 0 行；不新增/删除任何角色或 AS-BOT 行。
-- 字面量里一律不写 ASCII 分号（本仓迁移按分号切句顺序执行，字符串里的 ; 会把句子切坏）。
-- 与种子迁移同批：waker_migration.sql / waker_persona_capability_migration.sql /
-- waker_role_distinction_migration.sql / operations_manager_persona_migration.sql / as_bot_migration.sql
-- 里的同一写法已同步拆除（否则新环境初始化又把语言写回 persona）。

UPDATE `adh_as_bots`
SET `persona` = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(`persona`,
        '，中文回答；', '；'),
        ', 中文回答', ','),
        '；中文回答、', '；'),
        '。中文回答，', '，'),
        '，中文回答', ''),
        ',中文回答', ''),
        '、中文回答', ''),
        -- 拆词留下的双分隔符 `,; `：本仓迁移按 ASCII 分号切句，字面量里不能直接写它，
        -- 故用 CHAR(59) 拼装（等价于 `,; ` → `; `）。
        CONCAT(',', CHAR(59), ' '), CONCAT(CHAR(59), ' ')),
        '，；', '；')
WHERE `persona` LIKE '%中文回答%' OR `persona` LIKE CONCAT('%,', CHAR(59), ' %') OR `persona` LIKE '%，；%';

-- system_prompt 同口径清理（存量行里也可能写着「回答使用中文」这类句子）
UPDATE `adh_as_bots`
SET `system_prompt` = REPLACE(REPLACE(`system_prompt`,
        '回答使用中文，', '回复语言按系统提示词「回复语言」段执行，'),
        '使用中文与用户沟通', '回复语言按系统提示词「回复语言」段执行')
WHERE `system_prompt` LIKE '%中文回答%' OR `system_prompt` LIKE '%使用中文%';

-- ── 核验（应用后手工执行，期望都为 0）────────────────────────────────────────
-- SELECT COUNT(*) FROM adh_as_bots WHERE persona LIKE '%中文回答%' OR persona LIKE '%使用中文%';
-- SELECT COUNT(*) FROM adh_as_bots WHERE system_prompt LIKE '%中文回答%' OR system_prompt LIKE '%使用中文%';

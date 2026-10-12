-- 知识管理：推荐问题 → 快速路径（quick_path）迁移（幂等，可重复执行）
--
-- 背景: 「知识管理」功能由「推荐问题」调整为「快速路径」：
--   每条知识不再是被动展示的样例问题，而是「查询方案指引」——
--   服务端按用户问题触发词命中后，把该路径 content(方案说明)注入 LLM 系统提示词
--   （见 backend/modules/mind/execution/quick_paths.py + prompt_composer.compose_system_prompt）。
--
-- 语义:
--   * 新增 knowledge_type='quick_path'；沿用 title(路径名)/content(查询方案说明)/
--     metadata(JSON: {triggers:[触发词], scenario:适用场景})/related_tables/priority。
--   * 历史 recommend_question 行迁移为 quick_path：保留 title/content，
--     触发词需管理员在「快速路径」页补登记后才会命中（迁移后 metadata.triggers 为空）。
--   * sql_pair / followup_case 类型不受影响。

-- 1) 列注释登记 quick_path 类型
ALTER TABLE `adh_knowledge_items`
  MODIFY COLUMN `knowledge_type` varchar(32)
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL
  COMMENT '知识类型: quick_path / instruction / sql_pair / recommend_question / followup_case';

-- 2) 历史推荐问题迁移为快速路径（幂等：仅处理仍是 recommend_question 的行）
UPDATE `adh_knowledge_items`
   SET `knowledge_type` = 'quick_path',
       `metadata` = JSON_SET(COALESCE(`metadata`, JSON_OBJECT()), '$.triggers', JSON_ARRAY())
 WHERE `knowledge_type` = 'recommend_question';

-- 3) 菜单显示名同步（知识管理 → 快速路径）
UPDATE `adh_menu_registry` SET `label` = '快速路径' WHERE `menu_key` = 'system:knowledge-management';

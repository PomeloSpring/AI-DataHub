-- ============================================================================
-- AS-BOT 与 Waker 统一改造迁移（幂等、可重复执行）
--
-- 背景: AS-BOT 与 Waker 已是同一载体（角色智能体，不同入口），独立的
-- 「AS-BOT 动作权限矩阵 + 审批通道」整体退役——写动作权限由菜单与功能权限码
-- (adh_perm_registry + adh_role_perms) + AS-BOT 工具授权直接把关执行。
--
-- 本迁移做四件事:
--   1. 删除动作/审批三表 + 仪表盘设计表的审批列;
--   2. Waker→AS-BOT 改名: adh_wakers→adh_as_bots、waker_key→as_bot_key;
--   3. 权限码/菜单键改名 (waker:*→asbot:*、system:wakers→system:as-bots);
--   4. __system_bot__ 哨兵数据清理 (会话归属不再按入口区分)。
--
-- 注意: 历史迁移文件(waker_migration.sql 等)保持原样不改，存量改名只在本迁移做;
-- 数据库口径与 services/.env 的 METADATA_DB_DATABASE 一致 (adh2)。
-- ============================================================================

USE adh2;

DELIMITER $$

-- 通用守卫: 表存在才执行 DDL（NULL/空串 = no-op，PREPARE FROM NULL 会报 1064）
DROP PROCEDURE IF EXISTS adh_asbot_ddl$$
CREATE PROCEDURE adh_asbot_ddl(IN ddl TEXT)
BEGIN
    IF ddl IS NOT NULL AND ddl <> '' THEN
        SET @ddl = ddl;
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$

-- 守卫: 列存在才执行 DDL
DROP PROCEDURE IF EXISTS adh_asbot_ddl_if_col$$
CREATE PROCEDURE adh_asbot_ddl_if_col(IN tbl VARCHAR(64), IN col VARCHAR(64),
                                      IN col_exists_ddl TEXT, IN col_absent_ddl TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = DATABASE() AND table_name = tbl AND column_name = col) THEN
        CALL adh_asbot_ddl(col_exists_ddl);
    ELSEIF col_absent_ddl IS NOT NULL AND col_absent_ddl <> '' THEN
        CALL adh_asbot_ddl(col_absent_ddl);
    END IF;
END$$

-- 守卫: 索引存在才执行 DDL
DROP PROCEDURE IF EXISTS adh_asbot_ddl_if_idx$$
CREATE PROCEDURE adh_asbot_ddl_if_idx(IN tbl VARCHAR(64), IN idx VARCHAR(64),
                                      IN idx_exists_ddl TEXT, IN idx_absent_ddl TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.statistics
               WHERE table_schema = DATABASE() AND table_name = tbl AND index_name = idx) THEN
        CALL adh_asbot_ddl(idx_exists_ddl);
    ELSEIF idx_absent_ddl IS NOT NULL AND idx_absent_ddl <> '' THEN
        CALL adh_asbot_ddl(idx_absent_ddl);
    END IF;
END$$

DELIMITER ;

-- ============================================================================
-- 1. 删除 AS-BOT 动作注册/角色×动作矩阵/审批流水三表
--    (adh_as_bot_dashboard_designs 保留 —— 设计草稿仍是事实源，仅去审批列)
-- ============================================================================
DROP TABLE IF EXISTS adh_as_bot_role_actions;
DROP TABLE IF EXISTS adh_as_bot_action_registry;
DROP TABLE IF EXISTS adh_as_bot_approvals;

-- 仪表盘设计表: 删除 approval_id 列(其唯一索引 uk_design_approval 随列级联删除)
CALL adh_asbot_ddl_if_col('adh_as_bot_dashboard_designs', 'approval_id',
    'ALTER TABLE adh_as_bot_dashboard_designs DROP COLUMN approval_id', NULL);

-- ============================================================================
-- 2. Waker→AS-BOT 改名: 表与字段
-- ============================================================================

-- 2.1 adh_wakers → adh_as_bots (仅当旧表在、新表不在时改名)
SET @rename_ok = (
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = DATABASE() AND table_name = 'adh_wakers'
) AND NOT (
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = DATABASE() AND table_name = 'adh_as_bots'
);
SET @ddl = IF(@rename_ok = 1,
    'RENAME TABLE adh_wakers TO adh_as_bots',
    'SELECT 1');
CALL adh_asbot_ddl(@ddl);

-- 2.2 waker_key → as_bot_key (逐表守卫: 旧列在且新列不在时 CHANGE)
-- adh_as_bots
CALL adh_asbot_ddl_if_col('adh_as_bots', 'waker_key',
    'ALTER TABLE adh_as_bots CHANGE COLUMN waker_key as_bot_key VARCHAR(64) NOT NULL COMMENT ''唯一英文标识''', NULL);
-- adh_conversations
CALL adh_asbot_ddl_if_col('adh_conversations', 'waker_key',
    'ALTER TABLE adh_conversations CHANGE COLUMN waker_key as_bot_key VARCHAR(64) NOT NULL DEFAULT ''''', NULL);
-- adh_agent_sessions
CALL adh_asbot_ddl_if_col('adh_agent_sessions', 'waker_key',
    'ALTER TABLE adh_agent_sessions CHANGE COLUMN waker_key as_bot_key VARCHAR(128) NOT NULL', NULL);

-- 2.3 索引改名: idx_user_waker_updated → idx_user_as_bot_updated (CHANGE 后索引定义自动跟随列)
CALL adh_asbot_ddl_if_idx('adh_conversations', 'idx_user_waker_updated',
    'ALTER TABLE adh_conversations DROP INDEX idx_user_waker_updated', NULL);
CALL adh_asbot_ddl_if_idx('adh_conversations', 'idx_user_as_bot_updated',
    NULL,
    'ALTER TABLE adh_conversations ADD INDEX idx_user_as_bot_updated (user_id, as_bot_key, updated_at)');

-- 2.4 uk_waker_key → uk_as_bot_key (adh_as_bots 唯一键)
CALL adh_asbot_ddl_if_idx('adh_as_bots', 'uk_waker_key',
    'ALTER TABLE adh_as_bots DROP INDEX uk_waker_key', NULL);
CALL adh_asbot_ddl_if_idx('adh_as_bots', 'uk_as_bot_key',
    NULL,
    'ALTER TABLE adh_as_bots ADD UNIQUE KEY uk_as_bot_key (as_bot_key)');
CALL adh_asbot_ddl_if_idx('adh_as_bots', 'uk_waker_role',
    'ALTER TABLE adh_as_bots RENAME INDEX uk_waker_role TO uk_as_bot_role', NULL);

-- ============================================================================
-- 3. 权限码 / 菜单键改名
--    waker:read→asbot:read、waker:manage→asbot:manage、system:wakers→system:as-bots
--    asbot:config(动作授权配置, 功能已删) 退役: is_active=0 + 角色授权回收
-- ============================================================================

-- 3.1 adh_perm_registry 权限码改名 (新码不存在时才改，防唯一键撞车)
UPDATE adh_perm_registry SET perm_code = 'asbot:read'
WHERE perm_code = 'waker:read'
  AND NOT EXISTS (SELECT 1 FROM (SELECT perm_code FROM adh_perm_registry) t WHERE t.perm_code = 'asbot:read');
UPDATE adh_perm_registry SET perm_code = 'asbot:manage'
WHERE perm_code = 'waker:manage'
  AND NOT EXISTS (SELECT 1 FROM (SELECT perm_code FROM adh_perm_registry) t WHERE t.perm_code = 'asbot:manage');

-- 3.2 接口路径改名 /api/admin/wakers* → /api/admin/as-bots*、/api/chat/wakers* → /api/chat/as-bots*
UPDATE adh_perm_registry
SET api_pattern = REPLACE(REPLACE(api_pattern,
    '/api/admin/wakers', '/api/admin/as-bots'),
    '/api/chat/wakers', '/api/chat/as-bots')
WHERE api_pattern LIKE '%wakers%';

-- 3.3 菜单键改名 system:wakers → system:as-bots，显示名「Waker 配置」→「AS-BOT 配置」
UPDATE adh_perm_registry SET menu_key = 'system:as-bots' WHERE menu_key = 'system:wakers';
UPDATE adh_menu_registry SET menu_key = 'system:as-bots' WHERE menu_key = 'system:wakers';
UPDATE adh_menu_registry SET label = 'AS-BOT 配置'
WHERE menu_key = 'system:as-bots' AND label <> 'AS-BOT 配置';

-- 3.4 asbot:config 退役(动作授权配置已随审批通道删除): 置失效 + 角色授权回收
UPDATE adh_perm_registry SET is_active = 0, ai_access = 'none', ai_action_key = '',
       description = '已退役: AS-BOT 动作权限并入菜单与功能权限码',
       ai_note = '已退役: 动作权限由菜单与功能权限码 + AS-BOT 工具授权直接把关'
WHERE perm_code = 'asbot:config';

-- 3.5 角色授权表同步改名 + 退役码回收
UPDATE adh_role_perms SET perm_code = 'asbot:read'
WHERE perm_code = 'waker:read'
  AND NOT EXISTS (SELECT 1 FROM (SELECT perm_code FROM adh_role_perms) t WHERE t.perm_code = 'asbot:read');
UPDATE adh_role_perms SET perm_code = 'asbot:manage'
WHERE perm_code = 'waker:manage'
  AND NOT EXISTS (SELECT 1 FROM (SELECT perm_code FROM adh_role_perms) t WHERE t.perm_code = 'asbot:manage');
DELETE FROM adh_role_perms WHERE perm_code = 'asbot:config';

-- 3.6 旧菜单授权模型兜底(adh_role_menus 存量库可能有历史行，也可能整表不存在——历史迁移
--     写的是 USE adh;，故守卫表存在才执行，不阻断主迁移)
SET @ddl = IF(EXISTS(SELECT 1 FROM information_schema.tables
                     WHERE table_schema = DATABASE() AND table_name = 'adh_role_menus'),
    'UPDATE adh_role_menus SET menu_key = ''system:as-bots'' WHERE menu_key = ''system:wakers''',
    'SELECT 1');
CALL adh_asbot_ddl(@ddl);

-- ============================================================================
-- 4. __system_bot__ 哨兵清理（会话历史合并共用，不再按入口区分）
--    存量会话归属归一为 ''（与 conversation_waker_key_migration 的"未标记"口径一致）；
--    系统助手专用配置行退役删除（运行时已不解析它）。
-- ============================================================================
UPDATE adh_conversations SET as_bot_key = '' WHERE as_bot_key = '__system_bot__';
UPDATE adh_agent_sessions SET as_bot_key = '' WHERE as_bot_key = '__system_bot__';
DELETE FROM adh_as_bots WHERE as_bot_key = '__system_bot__';

-- ============================================================================
-- 清理临时存储过程
-- ============================================================================
DROP PROCEDURE IF EXISTS adh_asbot_ddl;
DROP PROCEDURE IF EXISTS adh_asbot_ddl_if_col;
DROP PROCEDURE IF EXISTS adh_asbot_ddl_if_idx;

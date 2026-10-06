-- 角色×动作权限矩阵迁移（幂等: DDL 经 information_schema 守卫，可重复执行）
--
-- 为什么加 level/statement：M3 规则建模的权限语义是三值（allow/require_approval/deny），
-- 且拒绝原因必须**可解释给用户**（来自规则 statement，perm_link 同款纪律）。
-- is_allowed 保留作"发起权"布尔（allow/require_approval→1、deny→0），裁决兼容旧读方。
--
-- 可编辑源 = 系统本体 rules[]（type=permission），本表是**展开投影**
-- （`*` 通配规则 × 动作注册展开成 (role_id, action_key) 行），由
-- ontology_service.rebuild_role_action_matrix 级联整表重建，禁止独立编辑。
-- uk_role_action(role_id, action_key) 已存在，upsert 语义成立。
--
-- 注意：应用实际连的元数据库是 adh2（见 services/.env 的 METADATA_DB_DATABASE）。

USE adh2;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_as_bot_role_actions'
      AND COLUMN_NAME = 'level') = 0,
  'ALTER TABLE adh_as_bot_role_actions ADD COLUMN level VARCHAR(16) NOT NULL DEFAULT '''' COMMENT ''allow|require_approval|deny（M3 规则原文）'' AFTER is_allowed',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_as_bot_role_actions'
      AND COLUMN_NAME = 'statement') = 0,
  'ALTER TABLE adh_as_bot_role_actions ADD COLUMN statement VARCHAR(512) NOT NULL DEFAULT '''' COMMENT ''规则人话（拒绝原因可解释）'' AFTER level',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

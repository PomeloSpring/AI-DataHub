-- ============================================================================
-- 用户记忆迁移 (幂等, information_schema 守卫, 可重复执行)
--
-- 背景: 记忆按 **user_id** 归属、跨会话注入提示词, 只存稳定事实(偏好/口径/习惯),
-- 不存任何一次会话的上下文(见 backend/core/memory_service.validate_content)。
-- 两类行共存一表:
--   * 固定槽位 business_domain/preferred_chart/default_time_grain/output_format/
--     number_precision/role_context —— 每人每槽一行, entry_no 恒为 1, 同名覆盖;
--   * 浮动条目 correction/workflow —— 每人每类**多行**(entry_no 递增),
--     两类共享 5 条上限(memory_service.FLOATING_LIMIT), 超限由服务层拒绝。
--
-- 为什么必须有 entry_no: 早期唯一键 uk_user_slot(user_id, slot_key) 把"每人每槽一行"
-- 的约束错套到浮动条目上 —— 第二条 correction 直接撞 1062, "浮动条目上限 5 条"
-- 根本存不下。改为 uk_user_slot_entry(user_id, slot_key, entry_no):
-- 固定槽位仍由 DB 保证单值(entry_no=1), 浮动条目按 entry_no 放开多行。
--
-- 幂等性: CREATE TABLE IF NOT EXISTS + 列/索引均按 information_schema 守卫后执行。
-- ============================================================================

CREATE TABLE IF NOT EXISTS adh_user_memory (
  id BIGINT NOT NULL AUTO_INCREMENT,
  user_id BIGINT NOT NULL COMMENT '记忆属主 (adh_users.id) — 跨会话按用户归属',
  slot_key VARCHAR(32) NOT NULL COMMENT '固定槽位名或浮动条目类别',
  entry_no INT NOT NULL DEFAULT 1 COMMENT '同类浮动条目序号(固定槽位恒为 1)',
  content VARCHAR(300) NOT NULL COMMENT '记忆内容（单条上限 300 字符, 只存稳定事实）',
  source VARCHAR(16) NOT NULL DEFAULT 'auto' COMMENT 'auto=LLM学习 / manual=用户显式',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_user_slot_entry (user_id, slot_key, entry_no),
  KEY idx_user_updated (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户记忆（项目专属：偏好/口径/习惯，不存会话上下文）';

-- 早期版本无 entry_no 列时补齐
SET @has_col := (SELECT COUNT(*) FROM information_schema.COLUMNS
                 WHERE table_schema = DATABASE() AND table_name = 'adh_user_memory'
                   AND column_name = 'entry_no');
SET @ddl := IF(@has_col = 0,
  'ALTER TABLE adh_user_memory ADD COLUMN entry_no INT NOT NULL DEFAULT 1 COMMENT ''同类浮动条目序号(固定槽位恒为 1)'' AFTER slot_key',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 换装唯一键: 先删旧的 uk_user_slot(会挡住浮动条目多行), 再建含 entry_no 的新键
SET @has_old_key := (SELECT COUNT(DISTINCT INDEX_NAME) FROM information_schema.STATISTICS
                     WHERE table_schema = DATABASE() AND table_name = 'adh_user_memory'
                       AND INDEX_NAME = 'uk_user_slot');
SET @ddl_drop := IF(@has_old_key > 0,
  'ALTER TABLE adh_user_memory DROP INDEX uk_user_slot',
  'SELECT 1');
PREPARE stmt2 FROM @ddl_drop; EXECUTE stmt2; DEALLOCATE PREPARE stmt2;

SET @has_new_key := (SELECT COUNT(DISTINCT INDEX_NAME) FROM information_schema.STATISTICS
                     WHERE table_schema = DATABASE() AND table_name = 'adh_user_memory'
                       AND INDEX_NAME = 'uk_user_slot_entry');
SET @ddl_add := IF(@has_new_key = 0,
  'ALTER TABLE adh_user_memory ADD UNIQUE KEY uk_user_slot_entry (user_id, slot_key, entry_no)',
  'SELECT 1');
PREPARE stmt3 FROM @ddl_add; EXECUTE stmt3; DEALLOCATE PREPARE stmt3;

-- 存量浮动条目重排序号: 同 (user_id, slot_key) 的多行按 id 递增赋 entry_no,
-- 并把固定槽位统一压回 entry_no=1(旧表因 uk_user_slot 本就只能有一行, 此处为兜底)。
-- 槽位清单与 backend/core/memory_service.FIXED_SLOTS 同步维护。
UPDATE adh_user_memory m
JOIN (
  SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id, slot_key ORDER BY id) AS rn
  FROM adh_user_memory
) t ON t.id = m.id
SET m.entry_no = IF(m.slot_key IN ('business_domain','preferred_chart','default_time_grain',
                                   'output_format','number_precision','role_context'),
                    1, t.rn);

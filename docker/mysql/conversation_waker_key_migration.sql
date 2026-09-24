-- AS-BOT 会话历史：为 adh_conversations 增加 waker_key 归属列（幂等、可重复执行）。
-- 用途：区分 AS-BOT 系统助手会话（waker_key='__system_bot__'）与业务分析 chat 会话，
-- 使 AS-BOT 拥有独立的历史记录与恢复，不混入业务会话清单（也不反之）。
-- 不猜历史归属：存量会话 waker_key 默认 ''（视为业务/未标记），不影响读取。

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_conv_add_column$$
CREATE PROCEDURE adh_conv_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema=DATABASE() AND table_name=tbl AND column_name=col) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DROP PROCEDURE IF EXISTS adh_conv_add_index$$
CREATE PROCEDURE adh_conv_add_index(IN tbl VARCHAR(64), IN idx VARCHAR(64), IN definition TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.statistics
                   WHERE table_schema=DATABASE() AND table_name=tbl AND index_name=idx) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD ', definition);
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

CALL adh_conv_add_column('adh_conversations', 'waker_key', "VARCHAR(64) NOT NULL DEFAULT ''");
-- 覆盖"按用户 + 归属 + 更新时间倒序"的历史清单查询
CALL adh_conv_add_index('adh_conversations', 'idx_user_waker_updated',
                        'INDEX idx_user_waker_updated (user_id, waker_key, updated_at)');

DROP PROCEDURE adh_conv_add_column;
DROP PROCEDURE adh_conv_add_index;

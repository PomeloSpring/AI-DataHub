-- ============================================================================
-- retire_embedding_columns_migration.sql
--
-- 退役 6 张表的 embedding 向量列：向量检索已全量移除，这些列只剩零向量占位
-- 写入（_placeholder_embedding，已随本迁移从 template_service/metadata_sync/
-- term_service 删除），无任何读方。
--
-- 涉及表: adh_table_info / adh_column_metadata /
--         adh_business_terms / adh_table_relations / adh_sql_corrections
--
-- 部署顺序（重要）: 先上线不再写 embedding 的代码（重启 web），再执行本迁移。
--   新代码 + 旧列 = 正常（INSERT 不再列 embedding，列可空）；
--   旧代码 + 新列 = 写入报错（INSERT 引用已删列）。顺序不可反。
--
-- 幂等: 列存在才 DROP，可重复执行。
--
-- Run: mysql -h <mysql_host> -P <port> -u <user> -p <METADATA_DB_DATABASE> \
--        < retire_embedding_columns_migration.sql
-- ============================================================================

DROP PROCEDURE IF EXISTS adh_drop_embedding_col;

DELIMITER $$
CREATE PROCEDURE adh_drop_embedding_col(IN p_table VARCHAR(64))
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = p_table
          AND COLUMN_NAME = 'embedding'
    ) THEN
        SET @ddl = CONCAT('ALTER TABLE ', p_table, ' DROP COLUMN embedding');
        PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

CALL adh_drop_embedding_col('adh_table_info');
CALL adh_drop_embedding_col('adh_column_metadata');
CALL adh_drop_embedding_col('adh_business_terms');
CALL adh_drop_embedding_col('adh_table_relations');
CALL adh_drop_embedding_col('adh_sql_corrections');

DROP PROCEDURE IF EXISTS adh_drop_embedding_col;

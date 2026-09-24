-- Chat Attachments Object Storage Migration
-- 附件存储从本地磁盘迁移到 S3 兼容对象存储
-- storage_path 字段复用: 对象存储模式下存 object key,本地模式存绝对路径
-- 新增 storage_type 字段区分存储后端,便于迁移期兼容

USE adh;

-- 添加 storage_type 列(幂等)
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_chat_attachments'
      AND COLUMN_NAME = 'storage_type'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_chat_attachments ADD COLUMN storage_type VARCHAR(16) NOT NULL DEFAULT ''local'' COMMENT ''local | object'' AFTER storage_path',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 修改 storage_path 注释,兼容对象存储 key
SET @col_exists2 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_chat_attachments'
      AND COLUMN_NAME = 'storage_path'
);
SET @ddl2 = IF(@col_exists2 = 1,
    'ALTER TABLE adh_chat_attachments MODIFY COLUMN storage_path VARCHAR(512) NOT NULL COMMENT ''存储路径: 本地绝对路径 或 对象存储 key''',
    'SELECT 1');
PREPARE stmt2 FROM @ddl2;
EXECUTE stmt2;
DEALLOCATE PREPARE stmt2;

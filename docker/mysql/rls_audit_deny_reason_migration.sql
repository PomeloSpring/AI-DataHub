-- RLS 审计日志补"拒绝原因"列
-- 背景: 拒绝(deny)审计行此前只落 original_sql，拒绝原因在
-- query_executor._log_permission_audit 计算后被丢弃，审计页"拒绝访问"记录
-- 没有可下钻的详情（filtered_sql 为空导致前端不出"查看详情"）。
-- 新增 deny_reason 持久化拒绝原因，审计页拒绝记录同样提供"查看详情"。

-- 检查字段是否已存在（跟随当前 USE 的库，兼容 adh/adh2）
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'adh_rls_audit_logs'
      AND column_name = 'deny_reason'
);

-- 动态执行 ALTER TABLE
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE adh_rls_audit_logs ADD COLUMN deny_reason VARCHAR(512) DEFAULT NULL COMMENT ''拒绝原因(action=deny)'' AFTER filtered_sql',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

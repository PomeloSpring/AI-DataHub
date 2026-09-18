-- ============================================================================
-- 敏感字段 · "不能查询出来"(block) 合规屏蔽策略 (迁移 + 全局种子)
--
-- 落地多级安全护城河的敏感列屏蔽基线:
--   1. adh_sensitive_fields.mask_type ENUM 扩 'block'
--      full|partial|hash = 脱敏返回; block = 不能查询出来(结果剔除 + 显式请求拒绝)
--   2. 全局屏蔽种子: datasource_id=0, workspace_id=0, table_name='' 表示
--      "跨所有数据源、所有表按列名生效"的公认 PII 列; 执行侧取
--      "全局(0) ∪ 指定数据源" 并集。
--
-- 约定: 与其他迁移一致, DDL/种子幂等(MySQL 无 ADD COLUMN IF NOT EXISTS,
--   用 information_schema / 计数守卫)。permission_enforcer 对缺列有运行时回落。
-- Run: mysql -h <mysql_host> -P <port> -u <user> -p <METADATA_DB_DATABASE> \
--        < sensitive_block_migration.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. adh_sensitive_fields.mask_type ENUM 扩 'block' (MODIFY 幂等, 值集仅追加)
-- ----------------------------------------------------------------------------
SET @has_block := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_sensitive_fields'
      AND COLUMN_NAME = 'mask_type'
      AND COLUMN_TYPE LIKE '%block%'
);
SET @ddl := IF(@has_block = 0,
    'ALTER TABLE adh_sensitive_fields MODIFY COLUMN mask_type ENUM(''full'',''partial'',''hash'',''none'',''block'') DEFAULT ''partial'' COMMENT ''full=置空 partial=部分 hash=哈希 none=不处理 block=不能查询出来''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 2. 全局 block 种子 (公认 PII 列名; table_name='' = 对所有表按列名屏蔽)
--    仅纳入无歧义的 PII 列名; name/address 等易误伤列交由管理员按需增补。
--    幂等: 按 uk_field(workspace_id, datasource_id, table_name, column_name) 判重。
-- ----------------------------------------------------------------------------
INSERT INTO adh_sensitive_fields
    (workspace_id, datasource_id, table_name, column_name, sensitivity_level, mask_type, description)
SELECT * FROM (
    SELECT 0 AS w, 0 AS d, '' AS t, 'username'  AS c, 'high'     AS l, 'block' AS m, '全局合规屏蔽: 用户名'  AS desc_ UNION ALL
    SELECT 0, 0, '', 'user_name',  'high',     'block', '全局合规屏蔽: 用户名'  UNION ALL
    SELECT 0, 0, '', 'phone',      'high',     'block', '全局合规屏蔽: 手机号'  UNION ALL
    SELECT 0, 0, '', 'mobile',     'high',     'block', '全局合规屏蔽: 手机号'  UNION ALL
    SELECT 0, 0, '', 'tel',        'high',     'block', '全局合规屏蔽: 电话'    UNION ALL
    SELECT 0, 0, '', 'email',      'high',     'block', '全局合规屏蔽: 邮箱'    UNION ALL
    SELECT 0, 0, '', 'id_card',    'critical', 'block', '全局合规屏蔽: 身份证号' UNION ALL
    SELECT 0, 0, '', 'idcard',     'critical', 'block', '全局合规屏蔽: 身份证号' UNION ALL
    SELECT 0, 0, '', 'bank_card',  'critical', 'block', '全局合规屏蔽: 银行卡号' UNION ALL
    SELECT 0, 0, '', 'card_no',    'critical', 'block', '全局合规屏蔽: 卡号'    UNION ALL
    SELECT 0, 0, '', 'password',   'critical', 'block', '全局合规屏蔽: 密码'    UNION ALL
    SELECT 0, 0, '', 'passwd',     'critical', 'block', '全局合规屏蔽: 密码'
) seed
WHERE NOT EXISTS (
    SELECT 1 FROM adh_sensitive_fields s
    WHERE s.workspace_id = seed.w AND s.datasource_id = seed.d
      AND s.table_name = seed.t AND s.column_name = seed.c
);

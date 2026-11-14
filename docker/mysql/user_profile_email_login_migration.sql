-- ============================================================================
-- 用户姓名 + 邮箱登录迁移：adh_users 增两列
-- ----------------------------------------------------------------------------
-- 口径：
--   * display_name = 用户姓名（真实姓名/称呼），管理员在「添加/编辑用户」维护，
--     非必填；展示用（ui-resource-display §1：列表显示可读 name），登录标识不变。
--   * email_hash = 邮箱可检索指纹（HMAC-SHA256，密钥 ADH_SECRET_KEY）。
--     为什么必须加列：email 是 AES-256-GCM 加密存储且每次加密用随机 IV，
--     同一邮箱两次密文不同 —— 历史上用密文等值做唯一性检查从未命中过（缺陷），
--     邮箱登录也无法按密文查找。指纹是确定性盲索引：可等值检索、不含明文；
--     原值仍只存密文。指纹按归一化邮箱（去首尾空白 + 小写）计算。
--   * 存量行回填不放在本迁移（SQL 无法解开应用层密文），由
--     backend/modules/auth/services/auth_service.py:_backfill_email_hashes
--     在邮箱登录时幂等回填；解不开的存量密文跳过并留 WARNING（该行暂不可邮箱登录）。
--
-- 幂等：ADD COLUMN / ADD INDEX 先查 information_schema（MySQL 不支持 IF NOT EXISTS）。
-- ============================================================================

SET @ddl := (SELECT IF(COUNT(*) = 0,
    CONCAT('ALTER TABLE adh_users ADD COLUMN display_name VARCHAR(64) NOT NULL DEFAULT '''' ',
      'COMMENT ''用户姓名（真实姓名/称呼，展示用；空=未填写）'''),
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_users'
      AND COLUMN_NAME = 'display_name');
PREPARE s1 FROM @ddl; EXECUTE s1; DEALLOCATE PREPARE s1;

SET @ddl := (SELECT IF(COUNT(*) = 0,
    CONCAT('ALTER TABLE adh_users ADD COLUMN email_hash CHAR(64) NOT NULL DEFAULT '''' ',
      'COMMENT ''邮箱可检索指纹 HMAC-SHA256（归一化邮箱；空=未填写/未回填）'''),
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_users'
      AND COLUMN_NAME = 'email_hash');
PREPARE s2 FROM @ddl; EXECUTE s2; DEALLOCATE PREPARE s2;

SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_users ADD INDEX idx_email_hash (email_hash)',
    'SELECT 1') FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_users'
      AND INDEX_NAME = 'idx_email_hash');
PREPARE s3 FROM @ddl; EXECUTE s3; DEALLOCATE PREPARE s3;

-- 注意：本批只加自己拥有的两列一索引；display_name/email_hash 的写入一律走
-- auth_service（create_user/update_user/邮箱登录回填），不得整行覆盖别的批次拥有的列。

-- ============================================================================
-- 核验（人工执行，不参与建库）
--   SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS
--     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_users'
--       AND COLUMN_NAME IN ('display_name', 'email_hash');
--   预期：两行均存在。
--   SELECT INDEX_NAME FROM information_schema.STATISTICS
--     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_users'
--       AND INDEX_NAME = 'idx_email_hash';
--   预期：idx_email_hash 存在。
-- ============================================================================

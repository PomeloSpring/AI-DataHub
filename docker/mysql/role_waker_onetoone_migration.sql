-- 角色-Waker 一对一绑定改造
-- 背景: 此前角色-Waker 是多对多(adh_role_wakers),现收敛为一对一
-- 一个角色只能有一个 Waker,创建角色时自动创建同名 Waker 并绑定
-- adh_wakers 新增 role_id 字段(唯一索引),删除 adh_role_wakers 表

USE adh;

-- ============================================================================
-- 1. adh_wakers 新增 role_id 字段
-- ============================================================================
-- role_id: 关联角色 ID,唯一索引确保一对一
-- 0 或 NULL = 无角色关联(如 __system_bot__ 特殊 Waker,后续工具化后也关联 admin 角色)

-- 检查字段是否已存在
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = 'adh'
      AND table_name = 'adh_wakers'
      AND column_name = 'role_id'
);

-- 动态执行 ALTER TABLE
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE adh_wakers ADD COLUMN role_id BIGINT DEFAULT NULL COMMENT ''关联角色ID(一对一;NULL=无角色关联)'' AFTER is_builtin',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ============================================================================
-- 2. 添加唯一索引
-- ============================================================================
SET @idx_exists = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = 'adh'
      AND table_name = 'adh_wakers'
      AND index_name = 'uk_waker_role'
);

SET @sql = IF(@idx_exists = 0,
    'ALTER TABLE adh_wakers ADD UNIQUE INDEX uk_waker_role (role_id)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ============================================================================
-- 3. 数据迁移: 将现有 adh_role_wakers 绑定关系迁移到 adh_wakers.role_id
-- ============================================================================
-- 注意: 如果角色已绑定多个 Waker,只保留第一个(按 id 排序)
-- 其余 Waker 的 role_id 置 NULL(成为无主 Waker,可在 WakerManager 手动关联或删除)
UPDATE adh_wakers w
    INNER JOIN (
        SELECT rw.waker_id, MIN(rw.role_id) AS role_id
        FROM adh_role_wakers rw
        GROUP BY rw.waker_id
    ) rw ON w.id = rw.waker_id
SET w.role_id = rw.role_id
WHERE w.role_id IS NULL;

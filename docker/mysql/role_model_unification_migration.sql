-- ============================================================================
-- 角色-权限数据模型收口迁移（幂等，可重复执行）
--
-- 背景（2026-10 缺陷修复 + 模型收口）：
--   1) adh_role_perms / adh_role_dashboard_access / adh_role_rls_policies 的 id 列
--      为 32 位 INT，系统 id 是 16 位时间戳大数（如角色 1791524185193618），
--      写入被 MySQL 钳位为 2147483647 且 INSERT IGNORE 吞掉警告 → 保存假成功
--      （看板勾选不生效 / 功能权限不生效 / RLS 绑定不生效）。列统一升 BIGINT。
--   2) 用户-角色模型收口（设计口径）：adh_user_roles 是用户→角色唯一真值源；
--      adh_users.role_id 绑定角色 id（user_role 名列为显示缓存）；
--      **工作空间角色已废除**（adh_user_roles.workspace_id 维度与 adh_workspace_roles 一并清退）。
--
-- 幂等性：ALTER 经 information_schema 判列/判索引后动态执行，重复执行无副作用。
-- ============================================================================

-- ── 1. 授权绑定表 id 升 BIGINT（修复 INT32 钳位） ─────────────────────────

SET @ddl := (SELECT IF(COUNT(*) > 0,
    'ALTER TABLE adh_role_perms MODIFY COLUMN role_id BIGINT NOT NULL',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_role_perms'
      AND COLUMN_NAME = 'role_id' AND DATA_TYPE = 'int');
PREPARE s1 FROM @ddl; EXECUTE s1; DEALLOCATE PREPARE s1;

SET @ddl := (SELECT IF(COUNT(*) > 0,
    'ALTER TABLE adh_role_dashboard_access MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN role_id BIGINT NOT NULL, MODIFY COLUMN dashboard_id BIGINT NOT NULL',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_role_dashboard_access'
      AND COLUMN_NAME = 'role_id' AND DATA_TYPE = 'int');
PREPARE s2 FROM @ddl; EXECUTE s2; DEALLOCATE PREPARE s2;

SET @ddl := (SELECT IF(COUNT(*) > 0,
    'ALTER TABLE adh_role_rls_policies MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN role_id BIGINT NOT NULL, MODIFY COLUMN policy_id BIGINT NOT NULL',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_role_rls_policies'
      AND COLUMN_NAME = 'role_id' AND DATA_TYPE = 'int');
PREPARE s3 FROM @ddl; EXECUTE s3; DEALLOCATE PREPARE s3;

SET @ddl := (SELECT IF(COUNT(*) > 0,
    'ALTER TABLE adh_role_dashboard_groups MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN role_id BIGINT NOT NULL, MODIFY COLUMN group_id BIGINT NOT NULL',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_role_dashboard_groups'
      AND COLUMN_NAME = 'role_id' AND DATA_TYPE = 'int');
PREPARE s3b FROM @ddl; EXECUTE s3b; DEALLOCATE PREPARE s3b;

DELETE FROM adh_role_dashboard_groups
 WHERE role_id = 2147483647 OR group_id = 2147483647;

-- ── 2. adh_users 绑定角色 id（user_role 名列降级为显示缓存） ──────────────

SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_users ADD COLUMN role_id BIGINT NULL AFTER user_role',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_users'
      AND COLUMN_NAME = 'role_id');
PREPARE s4 FROM @ddl; EXECUTE s4; DEALLOCATE PREPARE s4;

-- 回填：按显示名对齐角色 id（两表 collation 不同，统一 CONVERT 后比较）
UPDATE adh_users u JOIN adh_roles r
       ON CONVERT(r.name USING utf8mb4) COLLATE utf8mb4_0900_ai_ci
        = CONVERT(u.user_role USING utf8mb4) COLLATE utf8mb4_0900_ai_ci
   SET u.role_id = r.id
 WHERE u.role_id IS NULL OR u.role_id != r.id;

-- ── 3. adh_user_roles 去工作空间语义（唯一真值源收口） ───────────────────

SET @ddl := (SELECT IF(COUNT(*) > 0,
    'DELETE FROM adh_user_roles WHERE workspace_id != 0',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_user_roles'
      AND COLUMN_NAME = 'workspace_id');
PREPARE s0 FROM @ddl; EXECUTE s0; DEALLOCATE PREPARE s0;

SET @ddl := (SELECT IF(COUNT(*) = 0,
    'ALTER TABLE adh_user_roles ADD UNIQUE KEY uk_user_role (user_id, role_id)',
    'SELECT 1') FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_user_roles'
      AND INDEX_NAME = 'uk_user_role');
PREPARE s5 FROM @ddl; EXECUTE s5; DEALLOCATE PREPARE s5;

SET @ddl := (SELECT IF(COUNT(*) > 0,
    'ALTER TABLE adh_user_roles DROP INDEX uk_user_role_ws',
    'SELECT 1') FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_user_roles'
      AND INDEX_NAME = 'uk_user_role_ws');
PREPARE s6 FROM @ddl; EXECUTE s6; DEALLOCATE PREPARE s6;

SET @ddl := (SELECT IF(COUNT(*) > 0,
    'ALTER TABLE adh_user_roles DROP COLUMN workspace_id',
    'SELECT 1') FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_user_roles'
      AND COLUMN_NAME = 'workspace_id');
PREPARE s7 FROM @ddl; EXECUTE s7; DEALLOCATE PREPARE s7;

-- 工作空间角色表废除（工作空间角色语义已下线）
DROP TABLE IF EXISTS adh_workspace_roles;

-- ── 4. 镜像补齐（列有值镜像无行的历史缺口，如建号只写列的用户） ─────────

INSERT IGNORE INTO adh_user_roles (id, user_id, role_id)
SELECT UUID_SHORT(), u.id, u.role_id
  FROM adh_users u
 WHERE u.role_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM adh_user_roles r
                    WHERE r.user_id = u.id AND r.role_id = u.role_id);

-- ── 5. 钳位垃圾数据修复 ─────────────────────────────────────────────────

-- 功能权限：role_id 被钳位为 2147483647 的行内容（perm_code）完好，
-- 归属唯一超出 INT32 的角色（当前仅运营管理），按 MIN(超界角色) 恢复。
UPDATE adh_role_perms
   SET role_id = (SELECT MIN(id) FROM adh_roles WHERE id > 2147483647)
 WHERE role_id = 2147483647
   AND (SELECT COUNT(*) FROM adh_roles WHERE id > 2147483647) = 1;

-- 看板绑定：role_id 与 dashboard_id 双双钳位，真值不可恢复，删除脏行
-- （原勾选需在角色权限页重新勾选）。
DELETE FROM adh_role_dashboard_access
 WHERE role_id = 2147483647 OR dashboard_id = 2147483647;

-- RLS 绑定同理（如有脏行一并清）。
DELETE FROM adh_role_rls_policies
 WHERE role_id = 2147483647 OR policy_id = 2147483647;

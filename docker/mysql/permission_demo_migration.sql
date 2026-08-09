-- ═══════════════════════════════════════════════════════════════
-- 权限系统演示数据 — 完整的用户/角色/权限/RLS 策略
-- 执行: mysql -u root -p < permission_demo_migration.sql
-- ═══════════════════════════════════════════════════════════════

USE adh;

-- ============================================================================
-- 1. 演示用户 (密码都是 admin123)
-- ============================================================================
-- admin 用户已存在 (id=1)

INSERT IGNORE INTO adh_users (id, username, password_hash, email, user_role, status, created_at, updated_at)
VALUES
    (10, 'zhangsan', '$2b$12$LJ3m4ys3Lz0YBNOURq0Y3OjCfKJmKPOJYqDTPVCKzLOBhZMHfWO6e', 'zhangsan@example.com', 'analyst', 'active', NOW(), NOW()),
    (11, 'lisi', '$2b$12$LJ3m4ys3Lz0YBNOURq0Y3OjCfKJmKPOJYqDTPVCKzLOBhZMHfWO6e', 'lisi@example.com', 'viewer', 'active', NOW(), NOW()),
    (12, 'wangwu', '$2b$12$LJ3m4ys3Lz0YBNOURq0Y3OjCfKJmKPOJYqDTPVCKzLOBhZMHfWO6e', 'wangwu@example.com', 'analyst', 'active', NOW(), NOW()),
    (13, 'zhaoliu', '$2b$12$LJ3m4ys3Lz0YBNOURq0Y3OjCfKJmKPOJYqDTPVCKzLOBhZMHfWO6e', 'zhaoliu@example.com', 'viewer', 'active', NOW(), NOW());

-- ============================================================================
-- 2. 演示工作空间
-- ============================================================================

INSERT IGNORE INTO adh_workspaces (id, name, description, icon, color, owner_id, is_default, created_at, updated_at)
VALUES
    (1, '默认工作空间', '系统默认工作空间，包含所有数据', '🏠', '#1890ff', 1, 1, NOW(), NOW()),
    (100, '华东数据', '华东区域数据分析工作空间', '🌊', '#52c41a', 1, 0, NOW(), NOW()),
    (200, '华南数据', '华南区域数据分析工作空间', '🌴', '#fa8c16', 1, 0, NOW(), NOW()),
    (300, '全国数据', '全国数据总览工作空间', '🗺️', '#722ed1', 1, 0, NOW(), NOW());

-- ============================================================================
-- 3. 工作空间用户关联
-- ============================================================================

INSERT IGNORE INTO adh_workspace_users (workspace_id, user_id, role, is_default, joined_at)
VALUES
    -- admin 拥有所有工作空间
    (1, 1, 'owner', 1, NOW()),
    (100, 1, 'owner', 0, NOW()),
    (200, 1, 'owner', 0, NOW()),
    (300, 1, 'owner', 0, NOW()),
    -- zhangsan → 华东数据 (analyst)
    (100, 10, 'admin', 1, NOW()),
    (300, 10, 'member', 0, NOW()),
    -- lisi → 华南数据 (viewer)
    (200, 11, 'member', 1, NOW()),
    -- wangwu → 全国数据 (analyst)
    (300, 12, 'admin', 1, NOW()),
    (100, 12, 'member', 0, NOW()),
    (200, 12, 'member', 0, NOW()),
    -- zhaoliu → 华东数据 (viewer, 只读)
    (100, 13, 'member', 1, NOW());

-- ============================================================================
-- 4. 自定义角色 (补充系统内置的 admin/analyst/viewer)
-- ============================================================================

INSERT IGNORE INTO adh_roles (id, name, display_name, description, is_system, created_at, updated_at)
VALUES
    (100, 'region_analyst', '区域分析师', '只能查看本区域数据，可看分析报表', 0, NOW(), NOW()),
    (200, 'data_viewer', '数据查看者', '只能查看部分表，敏感列脱敏', 0, NOW(), NOW()),
    (300, 'full_analyst', '全量分析师', '可查看所有区域数据', 0, NOW(), NOW());

-- ============================================================================
-- 5. 用户-角色分配 (adh_user_roles)
-- ============================================================================

INSERT IGNORE INTO adh_user_roles (id, user_id, role_id, workspace_id, created_at)
VALUES
    -- admin → admin 角色 (全局)
    (1001, 1, 1, 0, NOW()),
    -- zhangsan → 区域分析师 (华东数据 工作空间)
    (1002, 10, 100, 100, NOW()),
    -- zhanganalyst → 全量分析师 (全国数据 工作空间)
    (1003, 10, 300, 300, NOW()),
    -- lisi → 数据查看者 (华南数据 工作空间)
    (1004, 11, 200, 200, NOW()),
    -- wangwu → 全量分析师 (全国数据 工作空间)
    (1005, 12, 300, 300, NOW()),
    -- wangwu → 区域分析师 (华东数据 工作空间)
    (1006, 12, 100, 100, NOW()),
    -- zhaoliu → 数据查看者 (华东数据 工作空间)
    (1007, 13, 200, 100, NOW());

-- ============================================================================
-- 6. 角色数据范围属性 (adh_role_attributes)
-- 用于 RLS 动态行过滤
-- ============================================================================

INSERT IGNORE INTO adh_role_attributes (id, role_id, workspace_id, attr_key, attr_value, created_at)
VALUES
    -- 区域分析师 → 华东
    (2001, 100, 100, 'region', '华东', NOW()),
    -- 数据查看者 → 华南
    (2002, 200, 200, 'region', '华南', NOW()),
    -- 全量分析师 → 全部区域 (空值 = 不过滤)
    (2003, 300, 300, 'region', '', NOW());

-- ============================================================================
-- 7. 工作空间-角色授权 (adh_workspace_roles)
-- ============================================================================

INSERT IGNORE INTO adh_workspace_roles (id, workspace_id, role_id, created_at)
VALUES
    -- 华东数据 工作空间允许 区域分析师
    (3001, 100, 100, NOW()),
    -- 华南数据 工作空间允许 数据查看者
    (3002, 200, 200, NOW()),
    -- 全国数据 工作空间允许 全量分析师
    (3003, 300, 300, NOW()),
    -- 所有工作空间都允许 admin
    (3004, 1, 1, NOW()),
    (3005, 100, 1, NOW()),
    (3006, 200, 1, NOW()),
    (3007, 300, 1, NOW());

-- ============================================================================
-- 8. 数据源权限 (adh_role_datasource_access)
-- 假设 datasource_id=1 是主要数据源
-- ============================================================================

INSERT IGNORE INTO adh_role_datasource_access (id, role_id, datasource_id, access_type, created_at)
VALUES
    -- 区域分析师 → 数据源1 读权限
    (4001, 100, 1, 'read', NOW()),
    -- 数据查看者 → 数据源1 读权限
    (4002, 200, 1, 'read', NOW()),
    -- 全量分析师 → 数据源1 读权限
    (4003, 300, 1, 'read', NOW());

-- ============================================================================
-- 9. 表权限 (adh_role_table_access)
-- ============================================================================

INSERT IGNORE INTO adh_role_table_access (id, role_id, datasource_id, table_name, access_type, created_at)
VALUES
    -- 区域分析师 → orders, users, products (读)
    (5001, 100, 1, 'orders', 'read', NOW()),
    (5002, 100, 1, 'users', 'read', NOW()),
    (5003, 100, 1, 'products', 'read', NOW()),
    -- 数据查看者 → orders 只读 (不能看 users/products)
    (5004, 200, 1, 'orders', 'read', NOW()),
    -- 全量分析师 → 所有表
    (5005, 300, 1, 'orders', 'read', NOW()),
    (5006, 300, 1, 'users', 'read', NOW()),
    (5007, 300, 1, 'products', 'read', NOW()),
    (5008, 300, 1, 'payments', 'read', NOW());

-- ============================================================================
-- 10. 列权限 (adh_role_column_access)
-- ============================================================================

INSERT IGNORE INTO adh_role_column_access (id, role_id, datasource_id, table_name, column_name, access_type, mask_pattern, created_at)
VALUES
    -- 数据查看者 → orders.amount 隐藏
    (6001, 200, 1, 'orders', 'amount', 'hidden', '', NOW()),
    -- 数据查看者 → users.phone 脱敏
    (6002, 200, 1, 'users', 'phone', 'masked', 'partial', NOW()),
    -- 数据查看者 → users.email 脱敏
    (6003, 200, 1, 'users', 'email', 'masked', 'partial', NOW()),
    -- 区域分析师 → users.phone 脱敏 (部分可见)
    (6004, 100, 1, 'users', 'phone', 'masked', 'partial', NOW());

-- ============================================================================
-- 11. RLS 行级安全策略 (adh_rls_policies)
-- ============================================================================

INSERT IGNORE INTO adh_rls_policies (id, name, description, workspace_id, datasource_id, table_name, policy_type, filter_type, filter_expr, user_attribute, is_active, created_by, created_at, updated_at)
VALUES
    -- 华东数据 工作空间 → 只看华东数据
    (7001, '华东区域过滤', '华东数据工作空间只看华东区域数据', 100, 1, 'orders', 'both', 'user_attribute', 'region = :user_region', 'region', 1, 1, NOW(), NOW()),
    (7002, '华东用户过滤', '华东数据工作空间只看华东用户', 100, 1, 'users', 'both', 'user_attribute', 'region = :user_region', 'region', 1, 1, NOW(), NOW()),
    -- 华南数据 工作空间 → 只看华南数据
    (7003, '华南区域过滤', '华南数据工作空间只看华南区域数据', 200, 1, 'orders', 'both', 'user_attribute', 'region = :user_region', 'region', 1, 1, NOW(), NOW()),
    (7004, '华南用户过滤', '华南数据工作空间只看华南用户', 200, 1, 'users', 'both', 'user_attribute', 'region = :user_region', 'region', 1, 1, NOW(), NOW()),
    -- 全国数据 工作空间 → 不过滤 (全量可见)
    (7005, '全国订单', '全国数据工作空间可看所有订单', 300, 1, 'orders', 'row', 'condition', '', '', 1, 1, NOW(), NOW());

-- ============================================================================
-- 12. RLS 列级策略 (adh_rls_column_policies)
-- ============================================================================

INSERT IGNORE INTO adh_rls_column_policies (id, policy_id, column_name, access_type, mask_pattern, description)
VALUES
    -- 华东策略 → users.salary 隐藏
    (8001, 7002, 'salary', 'hidden', '', '华东用户不看薪资'),
    -- 华东策略 → users.phone 脱敏
    (8002, 7002, 'phone', 'masked', 'partial', '华东用户电话脱敏'),
    -- 华南策略 → users.salary 隐藏
    (8003, 7004, 'salary', 'hidden', '', '华南用户不看薪资'),
    -- 华南策略 → orders.amount 脱敏
    (8004, 7003, 'amount', 'masked', 'partial', '华南订单金额脱敏');

-- ============================================================================
-- 13. RLS 用户属性 (adh_rls_user_attributes)
-- 备用: 当角色属性不够时，可直接给用户设置属性
-- ============================================================================

INSERT IGNORE INTO adh_rls_user_attributes (id, user_id, workspace_id, attr_key, attr_value, created_at, updated_at)
VALUES
    -- zhangsan 在华东数据工作空间 → region=华东
    (9001, 10, 100, 'region', '华东', NOW(), NOW()),
    -- lisi 在华南数据工作空间 → region=华南
    (9002, 11, 200, 'region', '华南', NOW(), NOW()),
    -- wangwu 在华东数据工作空间 → region=华东
    (9003, 12, 100, 'region', '华东', NOW(), NOW()),
    -- zhaoliu 在华东数据工作空间 → region=华东
    (9004, 13, 100, 'region', '华东', NOW(), NOW());

-- ============================================================================
-- 完成！
-- ============================================================================
-- 演示场景:
--
-- 用户       | 角色           | 工作空间     | 数据权限
-- ----------|---------------|-------------|----------------------------------
-- admin     | admin         | 全部         | 全部权限，无限制
-- zhangsan  | region_analyst| 华东数据     | 只看华东数据，phone脱敏
-- zhangsan  | full_analyst  | 全国数据     | 看全国数据，无限制
-- lisi      | data_viewer   | 华南数据     | 只看华南orders，salary隐藏，phone脱敏
-- wangwu    | full_analyst  | 全国数据     | 看全国数据，无限制
-- wangwu    | region_analyst| 华东数据     | 只看华东数据，phone脱敏
-- zhaoliu   | data_viewer   | 华东数据     | 只看华东orders，amount隐藏
--
-- RLS 策略:
-- - 华东工作空间 → WHERE region = '华东'
-- - 华南工作空间 → WHERE region = '华南'
-- - 全国工作空间 → 无过滤
--
-- 列级策略:
-- - data_viewer → salary隐藏, phone/email脱敏
-- - region_analyst → phone脱敏

-- ============================================================================
-- admin 角色数据源全量授权种子（纯角色 fail-closed 的前置授权）
-- ============================================================================
-- 背景:
--   “工作空间解绑数据源”改造后, 数据源可用集的唯一裁决 = 用户角色授权
--   (adh_user_roles ⋈ adh_role_datasource_access, 经
--    role_service.get_user_allowed_datasources 解析)。admin 不再 bypass 该裁决,
--   且空角色授权一律 fail-closed(空集, 不解释为全量)。
--   改造前 adh_role_datasource_access 可能为空(admin 靠工作空间绑定/bypass 取数),
--   若直接上线, admin 将访问不了任何数据源(锁死)。
--
-- 作用:
--   一次性、显式地把“全部数据源”授权给 admin 角色(name='admin'), 防止上线锁死。
--   这是显式种子(非静默迁移), 符合“安全策略变更不静默迁移线上配置”的既有决策。
--
-- 幂等性(可重复执行):
--   依赖 UNIQUE KEY uk_role_ds(role_id, datasource_id) + INSERT IGNORE:
--   已存在的 (admin, 数据源) 授权被忽略; 新增数据源后重跑会自动补齐。
--
-- id 生成策略:
--   adh_role_datasource_access.id 非自增。本种子取 id = 数据源 id
--   (adh_datasources 主键, 天然唯一且稳定), 因此:
--     * 每行 id 唯一(单角色 × 唯一数据源);
--     * 重跑 / 数据源增长时 id 稳定不漂移(INSERT IGNORE 只补新数据源);
--     * 与应用侧 role_service._gen_id()(微秒时间戳, 量级 ~1e15)不同量级
--       (数据源 id 为毫秒时间戳, 量级 ~1e12), 不会碰撞。
--
-- 影响范围: 仅 admin 角色; 不改动其它角色的既有授权(其它角色由角色权限 UI 管理)。
-- 目标库: 运行时元库为 adh2(见 services/.env METADATA_DB_DATABASE), 与 observability_migration.sql 一致。
-- ============================================================================

USE adh2;

INSERT IGNORE INTO adh_role_datasource_access (id, role_id, datasource_id, access_type, created_at)
SELECT d.id, r.id, d.id, 'read', NOW()
FROM adh_roles r
CROSS JOIN adh_datasources d
WHERE r.name = 'admin';

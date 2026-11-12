-- ============================================================================
-- App 使用分析看板（联耀）→ 内置角色数据集默认授权迁移（幂等，可重复执行）
--
-- 背景 (2026-11-12):
--   新建的 3 张看板（App 使用·自研 App 分析 / 三方集成 ES 口径 / 三方集成 RUM 侧）
--   共引用 23 个本批数据集。看板图表取数通道收口后，"能挂哪些数据集"由
--   `adh_role_dataset_access` 裁决（fail-closed：空授权 ≠ 全量），
--   若不给内置角色默认授权，非管理员进看板会看到"图挂得上、清单里却没有"的空承诺。
--
-- 本迁移只做一件事：把**本批三张看板真正引用到的 active 数据集**默认授权给
--   analyst / viewer（维持"看板建好就能看"的现状预期）。管理员仍可在
--   「角色权限 → 数据集」按角色收回。
--   未被这三张看板引用的数据集**不放权**（不借迁移扩权，与
--   chart_dataset_default_grant_migration.sql 同一纪律，且作用域更窄）。
--
-- 不覆盖的部分（有意留给人工决策，不是遗漏）：
--   RUM 那 7 个数据集挂在 SLS 数据源上（prd-sls-cn/en/eu/jp/uk）。
--   数据源授权按环境治理口径由管理员在「角色权限 → 数据源」配置，
--   本迁移不放权；未授权时刷新会显式回抛候选数据源业务名（不静默换源）。
--
-- 幂等性: INSERT IGNORE + UNIQUE KEY uk_role_dataset(role_id, dataset_id)。
-- 不写 `USE adh;`：库名随连接配置（METADATA_DB_DATABASE）。
-- 执行：venv/bin/python logs/apply_migration.py docker/mysql/app_usage_dataset_default_grant_migration.sql
-- ============================================================================

INSERT IGNORE INTO adh_role_dataset_access (role_id, dataset_id)
SELECT r.id, c.source_id
FROM adh_roles r
JOIN (
    SELECT DISTINCT ch.source_id
    FROM adh_charts ch
    JOIN adh_dashboards d ON d.id = ch.dashboard_id
    WHERE ch.source_type = 'dataset'
      AND d.name IN ('App 使用·自研 App 分析',
                     'App 使用·三方集成（ES openApp 口径）',
                     'App 使用·三方集成（SLS RUM 侧）')
) c
JOIN adh_datasets ds ON ds.id = c.source_id AND ds.status = 'active'
WHERE r.name IN ('analyst', 'viewer');

-- 核验（执行后手工查）：
--   -- 应为 23：本批三张看板引用的数据集
--   SELECT COUNT(DISTINCT ch.source_id) FROM adh_charts ch
--     JOIN adh_dashboards d ON d.id = ch.dashboard_id
--    WHERE ch.source_type='dataset' AND d.name LIKE 'App 使用·%';
--   -- 应为 0：被本批看板引用但无任何内置角色授权的数据集
--   SELECT DISTINCT ch.source_id FROM adh_charts ch
--     JOIN adh_dashboards d ON d.id = ch.dashboard_id
--    WHERE ch.source_type='dataset' AND d.name LIKE 'App 使用·%'
--      AND NOT EXISTS (SELECT 1 FROM adh_role_dataset_access a WHERE a.dataset_id = ch.source_id);

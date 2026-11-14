-- ============================================================================
-- 外部合作方对接分析看板（联耀）→ 内置角色数据集默认授权迁移（幂等，可重复执行）
--
-- 背景 (2026-11-14)：本批新建 4 张看板（合作方对接·总览与推送 / 合作状态与生命周期 /
--   合作方获取（近 21 天）/ 获取趋势与动作），共引用「合作方对接·」前缀数据集。
--   看板图表取数通道收口后，"能挂哪些数据集"由 `adh_role_dataset_access` 裁决
--   （fail-closed：空授权 ≠ 全量）。不给内置角色默认授权，就会出现
--   "看板里图挂得上、数据集清单里却没有"的空承诺。
--
-- 与 docker/mysql/app_usage_dataset_default_grant_migration.sql 同一条纪律：
--   * 只放**本批四张看板真正引用到的 active 数据集**，未被引用的不放权（不借迁移扩权）；
--   * 放的是「数据集可见性」，**不放数据源授权**：analyst/viewer 在 `adh_role_datasource_access`
--     里仍只有 cloud-platform-es（+ analyst 的 prd-cn），五个日志项目（prd-sls-*）未授权，
--     本批不擅自扩权（用户 2026-11-14 明确的三条假设之一）。
--     因此非管理员刷新入向图时会拿到**指名候选数据源的显式拒绝**，不是空图、也不是静默换源；
--     要放开由管理员点名"放给哪个角色"后在「角色权限 → 数据源」配置。
--
-- 有意不做（不是遗漏，是需要人拍板的授权面）：
--   不给 analyst/viewer 放**看板可见性**（`adh_role_dashboard_access`）：现网该表只有 3 行，
--   既有 App 使用 / RUM 分析看板同样未放给内置角色 —— 非管理员进这些看板拿到的是
--   "无权访问该仪表盘"。本批不把"沿用既有口径"偷偷改成"扩大看板可见范围"。
--
-- 幂等性：INSERT IGNORE + UNIQUE KEY uk_role_dataset(role_id, dataset_id)。
-- 不写 `USE adh;`：库名随连接配置（METADATA_DB_DATABASE）。
-- 执行：venv/bin/python logs/apply_migration.py docker/mysql/isv_dataset_default_grant_migration.sql
-- ============================================================================

INSERT IGNORE INTO adh_role_dataset_access (role_id, dataset_id)
SELECT r.id, c.source_id
FROM adh_roles r
JOIN (
    SELECT DISTINCT ch.source_id
    FROM adh_charts ch
    JOIN adh_dashboards d ON d.id = ch.dashboard_id
    WHERE ch.source_type = 'dataset'
      AND d.name IN ('合作方对接·总览与推送',
                     '合作方对接·合作状态与生命周期',
                     '合作方对接·合作方获取（近 21 天）',
                     '合作方对接·获取趋势与动作')
) c
JOIN adh_datasets ds ON ds.id = c.source_id AND ds.status = 'active'
WHERE r.name IN ('analyst', 'viewer');

-- 核验（执行后手工查）：
--   -- 应为 21 中本批看板实际引用的条数：本批四张看板引用的数据集
--   SELECT COUNT(DISTINCT ch.source_id) FROM adh_charts ch
--     JOIN adh_dashboards d ON d.id = ch.dashboard_id
--    WHERE ch.source_type='dataset' AND d.name LIKE '合作方对接·%';
--   -- 应为 0：被本批看板引用但无任何内置角色授权的数据集
--   SELECT DISTINCT ch.source_id FROM adh_charts ch
--     JOIN adh_dashboards d ON d.id = ch.dashboard_id
--    WHERE ch.source_type='dataset' AND d.name LIKE '合作方对接·%'
--      AND NOT EXISTS (SELECT 1 FROM adh_role_dataset_access a WHERE a.dataset_id = ch.source_id);
--   -- 应为 0：本批没有借道给 analyst/viewer 加数据源授权
--   SELECT COUNT(*) FROM adh_role_datasource_access a
--     JOIN adh_roles r ON r.id=a.role_id
--    WHERE r.name IN ('analyst','viewer') AND a.datasource_id IN
--          (SELECT id FROM adh_datasources WHERE name LIKE 'prd-sls-%');

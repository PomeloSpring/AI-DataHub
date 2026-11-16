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
--   只放**本批四张看板真正引用到的 active 数据集**（按 adh_charts.source_id 反查，不拉整个前缀），
--   未被本批看板引用的数据集不放权 —— 不借迁移扩权。
--
-- 不覆盖的部分（有意留给人工决策，不是遗漏）：
--   数据源授权：analyst/viewer 在 `adh_role_datasource_access` 里仍只有 cloud-platform-es
--   （+ analyst 的 prd-cn），五个日志项目（prd-sls-*）一律不放 —— 本批不擅自扩数据源权
--   （用户 2026-11-14 明确的三条假设之一）。要放开需管理员点名"放给哪个角色"。
--
-- 有意不做（2026-11-14 用户拍板，不是遗漏）：
--   不给 analyst/viewer 放**看板可见性**（`adh_role_dashboard_access`）——
--   "新的看板默认 admin 可看就行，哪些人可看我会自己配置"。本批一度按方案验收判据 3
--   临时放开过这四张并实测了逐图行为（证据留档在 logs/isv_nonadmin_check.log /
--   logs/isv_nonadmin_check_viewer.log），拍板后已回滚
--   （logs/rollback_isv_dashboard_grants.py，只删本批四张 × analyst/viewer，幂等）。
--   ⇒ 判据 3 那句"显式提示候选数据源"要在管理员把可见性放开后才会出现在页面上；
--     实测结论仍然有效（出数 23 / 指名数据源显式拒 18 / 空图 0 / 静默换源 0），
--     只是"放权"这个动作不再由本批代持。
--   本文件第 1 节放的是**数据集可见性**（谁能用这些数据集取数），沿用 App 使用那批
--   的既定口径，未随本次拍板收回；要收就在「角色权限」页按角色调。
--
-- 幂等性：INSERT IGNORE + 唯一键 uk_role_dataset(role_id, dataset_id)。
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

-- ----------------------------------------------------------------------------
-- 2) 看板可见性：本批**不放**（用户拍板"新看板默认只 admin 可看"）
--    曾经放开过、已回滚；要放开请在「角色权限 → 看板」按角色配，不写回迁移里代持。
-- ----------------------------------------------------------------------------

-- 核验（执行后手工查）：
--   -- 应为 0：本批没有代持看板可见性（默认只 admin 可看）
--   SELECT COUNT(*) FROM adh_role_dashboard_access a JOIN adh_dashboards d ON d.id=a.dashboard_id
--    WHERE d.name LIKE '合作方对接·%';
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

-- ============================================================================
-- Operations Manager 角色 → SLS 数据源读授权补齐（幂等，可重复执行）
--
-- 背景 (2026-11-13，阶段二验收时实测到):
--   「Test」(1779849441251) 与「数字化大屏」(1780540626928) 两张看板直授给了
--   Operations Manager 角色，但整板刷新时有 4 张图报：
--     当前角色无权访问数据集「用户月操作日志量-2」的任何可用数据源
--     （可用范围：prd-sls-cn、prd-sls-en、prd-sls-jp、prd-sls-uk、prd-sls-eu）
--   数据集 30/31 的执行源是 SLS，而该角色只有 arms-prd-cn / arms-prd-en。
--   这与 ES 数据源迁移无关（今天走内置 ES 通道的那两张图反而是好的），属存量授权缺口，
--   由阶段二的多身份刷新验收探针暴露。
--
-- 与既有口径的关系（**这是对本批拍板的一次显式扩权，不是自动补齐**）：
--   `app_usage_dataset_default_grant_migration.sql` 明确把 SLS 数据源授权留给人工决策
--   （"数据源授权按环境治理口径由管理员在「角色权限 → 数据源」配置，本迁移不放权"）。
--   2026-11-13 需求方明确要求本批补上，故落地在此；管理员仍可在角色页按源收回。
--
-- 作用域刻意收窄（不借迁移扩权）：
--   只授 **Operations Manager 一个角色** + **prd-sls-* 五个源**，
--   理由是"它被直授的看板上引用的数据集正好需要这些源"。不给其他角色放，
--   也不授该角色没被引用到看板所需的任何源。
--   行/列级策略与敏感字段仍照常裁决 —— 本文件只解决"源进得去"。
--
-- 幂等性: INSERT IGNORE + UNIQUE KEY uk_role_ds(role_id, datasource_id)；
--         行主键复用 datasource_id（与 role_datasource_admin_seed.sql 同一手法，
--         该表 id 无 auto_increment，值只需唯一不需可读）。
-- 不写 `USE adh;`：库名随连接配置（METADATA_DB_DATABASE）。
-- 执行：venv/bin/python logs/apply_migration.py docker/mysql/ops_manager_sls_datasource_grant_migration.sql
-- ============================================================================

INSERT IGNORE INTO adh_role_datasource_access (id, role_id, datasource_id, access_type, created_at)
SELECT d.id, r.id, d.id, 'read', NOW()
FROM adh_roles r
JOIN adh_datasources d ON d.name IN ('prd-sls-cn', 'prd-sls-en', 'prd-sls-jp', 'prd-sls-uk', 'prd-sls-eu')
WHERE r.name = 'Operations Manager';

-- 核验（执行后手工查）：
--   -- 应为 8：arms-prd-cn、arms-prd-en、cloud-platform-es + 五个 prd-sls-*
--   SELECT d.name FROM adh_role_datasource_access a
--     JOIN adh_roles r ON r.id = a.role_id
--     JOIN adh_datasources d ON d.id = a.datasource_id
--    WHERE r.name = 'Operations Manager' ORDER BY d.name;
--   -- 应为 0：OM 被直授看板上引用、且执行源未授的已挂源数据集
--   --   （`datasource_id > 0` 是必要的：未迁移的 ES 数据集还挂在内置通道上（ds=0），
--   --     今天不经源授权裁决，把它们计进来会误报）
--   SELECT DISTINCT c.source_id FROM adh_charts c
--     JOIN adh_role_dashboard_access rd ON rd.dashboard_id = c.dashboard_id
--     JOIN adh_roles r ON r.id = rd.role_id
--     JOIN adh_datasets ds ON ds.id = c.source_id AND ds.datasource_id > 0
--    WHERE r.name = 'Operations Manager' AND c.source_type = 'dataset'
--      AND NOT EXISTS (
--        SELECT 1 FROM adh_role_datasource_access a
--         WHERE a.role_id = r.id AND a.datasource_id = ds.datasource_id);

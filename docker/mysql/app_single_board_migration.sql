-- ============================================================================
-- 「App 单应用看板」组合上线 → 标签词表统一 + 组合角色绑定 + 数据集默认授权
--
-- 背景 (2026-11-13):
--   「App 使用·自研 App 分析」「App 使用·三方集成」是横向比较用的**总集**，缺"这一个 App
--   现在怎么样"的单应用视角。本次由 logs/build_app_single_dashboards.py 建 24 张单 App 看板
--   （自研 6 / 三方集成 16 / 平台主应用 2）并归入新组合「App 单应用看板（联耀）」。
--   本迁移只负责**建板脚本不该代管的那三件事**（角色可见性、标签词表、数据集授权）。
--
-- 一、标签词表统一（防"两个近似标签并存"）
--   总集原先打的是 `自研APP` / `集成APP`，单应用板用 `自研App` / `三方集成App`——
--   大小写不同就是两个标签，看板清单的标签下拉会长出两行几乎一样的选项，
--   用户按其中一个筛就漏掉另一半。这里把总集改写到同一份词表并补 `App总集`，
--   单应用板由脚本打 `单App看板` + 同一套域标签。
--   ⚠ 标签只影响清单过滤，不参与取数与权限（可见集仍由服务端 fail-closed 裁决）。
--
-- 二、组合角色绑定：新组合与总集组合保持同一可见现状（只挂 admin）。
--   组只做编排不授予看板可见性（那是 adh_role_dashboard_access 的事），
--   这里补的是"这个组合出现在谁的看板清单里"。
--
-- 三、数据集默认授权：单应用板引用到的 active 数据集默认放给 analyst / viewer，
--   维持"看板建好就能看"；SLS 数据源授权仍归管理员在「角色权限 → 数据源」配置，
--   本迁移不放权（未授权时刷新会显式回抛候选源业务名，不静默换源）。
--
-- 幂等：UPDATE 按名定位且值不变即 0 行；INSERT IGNORE + UNIQUE KEY uk_role_dataset(role_id,dataset_id)
--       / uk_role_group(role_id,group_id)。可重复执行，终态一致。
-- 不写 `USE adh;`：库名随连接配置（METADATA_DB_DATABASE）。
-- 前置：先跑 logs/build_app_single_datasets.py 与 logs/build_app_single_dashboards.py
--       （本迁移按看板名/组合名定位，板和数据集不存在时相关语句自然 0 行，不会误改他处）。
-- 执行：venv/bin/python logs/apply_migration.py docker/mysql/app_single_board_migration.sql
-- ============================================================================

-- ── 一、标签词表统一（总集三张板）────────────────────────────────────────────
UPDATE adh_dashboards
   SET tags = JSON_ARRAY('App总集', '自研App')
 WHERE name = 'App 使用·自研 App 分析'
   AND CAST(tags AS CHAR) <> JSON_ARRAY('App总集', '自研App');

UPDATE adh_dashboards
   SET tags = JSON_ARRAY('App总集', '三方集成App')
 WHERE name IN ('App 使用·三方集成', 'App 使用·三方集成（ES openApp 口径）')
   AND CAST(tags AS CHAR) <> JSON_ARRAY('App总集', '三方集成App');

UPDATE adh_dashboards
   SET tags = JSON_ARRAY('App总集', '三方集成App')
 WHERE name IN ('App 使用·三方集成（RUM）', 'App 使用·三方集成（SLS RUM 侧）')
   AND CAST(tags AS CHAR) <> JSON_ARRAY('App总集', '三方集成App');

-- ── 二、组合编排：描述与新组合的角色可见性（与总集组合同口径）───────────────
-- 用 CONCAT 不用 `||`：本库 sql_mode 没开 PIPES_AS_CONCAT，`||` 会被当成逻辑或（拼出一串 0/1）
UPDATE adh_dashboard_groups
   SET description = CONCAT('联耀 App 使用情况总集（自研 / 三方集成 ES 口径 / 三方集成 RUM 侧）；',
                            '逐个 App 的独立看板在「App 单应用看板（联耀）」组合。')
 WHERE name = 'App 使用分析（联耀）'
   AND description NOT LIKE '%App 单应用看板%';

INSERT IGNORE INTO adh_role_dashboard_groups (role_id, group_id)
SELECT r.id, g.id
FROM adh_roles r
JOIN adh_dashboard_groups g ON g.name = 'App 单应用看板（联耀）'
WHERE r.name = 'admin';

-- ── 三、单应用板引用数据集 → analyst / viewer 默认授权 ───────────────────────
INSERT IGNORE INTO adh_role_dataset_access (role_id, dataset_id)
SELECT r.id, c.source_id
FROM adh_roles r
JOIN (
    SELECT DISTINCT ch.source_id
    FROM adh_charts ch
    JOIN adh_dashboards d ON d.id = ch.dashboard_id
    WHERE ch.source_type = 'dataset'
      AND d.name LIKE 'App·%'
) c ON c.source_id IS NOT NULL
JOIN adh_datasets ds ON ds.id = c.source_id AND ds.status = 'active'
WHERE r.name IN ('analyst', 'viewer');

-- ============================================================================
-- 核验（执行后逐条查）
-- ============================================================================
-- ① 标签词表里不应再有旧写法（应 0 行）。必须带 BINARY：默认 collation 大小写不敏感，
--    直接 LIKE '%自研APP%' 会把新标签 `自研App` 也匹配出来（看着像没改干净）：
--    SELECT id, name, tags FROM adh_dashboards
--     WHERE BINARY CAST(tags AS CHAR) LIKE '%自研APP%'
--        OR BINARY CAST(tags AS CHAR) LIKE '%集成APP%';
-- ② 新组合及其成员数（应为 24）：
--    SELECT g.id, g.name, COUNT(gi.dashboard_id) boards
--      FROM adh_dashboard_groups g
--      LEFT JOIN adh_dashboard_group_items gi ON gi.group_id = g.id
--     WHERE g.name = 'App 单应用看板（联耀）' GROUP BY g.id, g.name;
-- ③ 应为 0：单应用板引用但无任何内置角色授权的数据集
--    SELECT DISTINCT ch.source_id FROM adh_charts ch
--      JOIN adh_dashboards d ON d.id = ch.dashboard_id
--     WHERE ch.source_type='dataset' AND d.name LIKE 'App·%'
--       AND NOT EXISTS (SELECT 1 FROM adh_role_dataset_access a WHERE a.dataset_id = ch.source_id);
-- ④ 组合已对 admin 可见（应 1 行）：
--    SELECT r.name, g.name FROM adh_role_dashboard_groups b
--      JOIN adh_roles r ON r.id = b.role_id
--      JOIN adh_dashboard_groups g ON g.id = b.group_id
--     WHERE g.name = 'App 单应用看板（联耀）';

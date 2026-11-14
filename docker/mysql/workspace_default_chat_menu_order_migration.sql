-- ============================================================================
-- 工作空间默认页改「智能问数」: 侧栏顺序与角色菜单登记同批对齐
--
-- 背景:
--   * 侧栏「数据分析」组原本 数据看板(1) → 智能问数(2)（见 dashboard_workspace_menu_migration.sql），
--     工作空间默认页（登录落地 / 模块切换 / `/ws/:id` 首页）也跟着落看板清单；
--   * 现调整为 智能问数(1) → 数据看板(2)，默认页同时改为智能问数。
--     前端真值源：`WorkspaceLayout.tsx` 的 menuItems 顺序、`App.tsx` 的 `/ws/:id` index、
--     `WorkspaceRoute.tsx: workspaceLandingPath`。
--
-- 口径:
--   * 只动组内 sort，不动 label / section / perm_code / ai_access：菜单显隐仍由权限码反查
--     （workspace:chat ↔ chat:use，workspace:dashboards ↔ dashboard:read）；
--   * 不新增权限点、不补默认授权（两个菜单都已授给全部生效角色，无存量用户入口变化）；
--   * 旧独立看板域书签（/dashboards*）仍落看板清单，不随默认页移动（那是链接语义，不是默认页语义）；
--   * 「数据看板」入口的内容级可见性仍由 adh_role_dashboard_access 在数据层 fail-closed 裁决，本迁移不涉及。
--
-- 幂等: 按 menu_key 定点 UPDATE，可重复执行；重跑旧迁移（按文件名顺序在本文件之前）会被本文件覆盖。
--
-- 不写 `USE <db>;`（与 app_single_board_migration.sql 同写法）：本仓远端 MySQL 同实例上同时存在
--   `adh` 与 `adh2` 两个 schema，而本机 backend/.env 的 METADATA_DB_DATABASE=adh2；写死库名会把语句
--   打到另一套环境，而连接不报错、脚本还打印 "applied N statements"，是静默空操作（比报错更危险）。
--   库名只由连接配置决定，应用与核验必须连同一个库（先 SELECT DATABASE()）。
-- ============================================================================

-- 1) 数据分析组内排序与侧栏一致: 智能问数(1) → 数据看板(2) → 报告中心(3)
UPDATE adh_menu_registry SET sort = 1 WHERE menu_key = 'workspace:chat'      AND module = 'workspace';
UPDATE adh_menu_registry SET sort = 2 WHERE menu_key = 'workspace:dashboards' AND module = 'workspace';
UPDATE adh_menu_registry SET sort = 3 WHERE menu_key = 'workspace:reports'   AND module = 'workspace';

-- 2) 后续分组顺延值显式钉住（云空间 / 自动化），避免与组内项交错
UPDATE adh_menu_registry SET sort = 5 WHERE menu_key = 'workspace:assets'    AND module = 'workspace';
UPDATE adh_menu_registry SET sort = 6 WHERE menu_key = 'workspace:scheduled'  AND module = 'workspace';

-- 3) 核验（人工执行时逐条看）:
--    SELECT menu_key, section, sort FROM adh_menu_registry
--     WHERE module='workspace' AND is_active=1 ORDER BY sort;
--    期望: workspace:chat(1) / workspace:dashboards(2) / workspace:reports(3)
--          / workspace:assets(5) / workspace:scheduled(6)

-- ============================================================================
-- 数据中台「数据目录」段改名「数据资产」+ 段内重排 + 两处入口消失修复
--
-- 为什么需要它（不是文案美化）：
--   1) 段名与内容错配：该段装的是 数据集/本体建模/指标与维度/关系图谱/分析场景/
--      决策建议 —— 全部是"接进来之后形成、维护的资产"，而"数据目录（catalog）"
--      的本义（数据源 / 表&字段）已被上一段「数据源」占着（data:tables = catalog:read）。
--      只改这一个词能同时覆盖"物理取数载体（数据集）+ 语义口径资产"，故取「数据资产」。
--   2) 真实缺陷（本次同批修，用户 2026-11-20 拍板）：data:metrics 与
--      data:knowledge-graph 两行 is_active=0，且 metrics:read / graph:read 的
--      perm_registry.menu_key 都被挂在 data:ontology 上。按 /roles/current/permissions
--      的两路反查口径（菜单主码 ∪ 权限点归属），非 admin 角色（analyst 持
--      metrics:read / graph:read）侧栏看不到「指标与维度」「关系图谱」，
--      角色页也没有这两个菜单分组 —— 配了看不见、勾了没入口（menu-permission-sync §2.2/§6）。
--
-- 用法：
--   venv/bin/python logs/apply_ddl_local.py docker/mysql/data_assets_section_migration.sql
--   （不写 USE，库名只由连接配置决定；脚本会打印目标库，须为 adh2）
--   应用后跑文件末尾的核验 SELECT，并以库现状为准，不以脚本退出码为凭。
--
-- 幂等/边界：
--   * 全部为幂等 UPDATE，可重复执行；
--   * 只动 section / sort / label / is_active 这四个展示与可见性字段，
--     **不动任何 perm_code、不新增 adh_role_perms 授权** —— 权限码本身未变，
--     恢复可见不是扩权（analyst 本来就持有 metrics:read / graph:read）；
--   * graph:manage（本体页「重建命名图」按钮）与 metrics:manage 的归属不动：
--     前者服务本体建模页，后者本已登记在 data:metrics，菜单启用后自然从
--     「通用功能」归回该菜单组；
--   * 退役行（is_active=0）只做去重与段名收敛，不改变其可见性。
-- ============================================================================

-- ── 1. 段名与段内顺序（浏览型资产在前、配置型资产在后）──────────────────
--    数据集(4) → 本体建模(5) → 指标与维度(6) → 关系图谱(7) → 分析场景(8) → 决策建议(9)
--    段位衔接上一段「数据源」1-3，与后续安全(11)/质量(21)/同步(31)保持空档习惯。
UPDATE adh_menu_registry SET section = '数据资产', sort = 4 WHERE menu_key = 'data:datasets';
UPDATE adh_menu_registry SET section = '数据资产', sort = 5 WHERE menu_key = 'data:ontology';
UPDATE adh_menu_registry SET section = '数据资产', sort = 8 WHERE menu_key = 'data:scenario';
UPDATE adh_menu_registry SET section = '数据资产', sort = 9 WHERE menu_key = 'data:decisions';

-- ── 2. 修复入口消失：启用菜单行 + label 对齐侧栏 ─────────────────────────
UPDATE adh_menu_registry
SET section = '数据资产', sort = 6, label = '指标与维度', is_active = 1
WHERE menu_key = 'data:metrics';

UPDATE adh_menu_registry
SET section = '数据资产', sort = 7, label = '关系图谱', is_active = 1
WHERE menu_key = 'data:knowledge-graph';

-- ── 3. 权限点归属修正（角色页出现独立菜单分组，反查能命中）───────────────
--    metrics:read / graph:read 原先都挂在 data:ontology 下，是"本体建模"菜单的
--    权限点归属，导致两个菜单在角色页无卡片、非 admin 侧栏无入口。
UPDATE adh_perm_registry
SET menu_key = 'data:metrics', label = '查看指标与维度'
WHERE perm_code = 'metrics:read';

UPDATE adh_perm_registry
SET menu_key = 'data:knowledge-graph', label = '查看关系图谱'
WHERE perm_code = 'graph:read';

-- ── 4. label 漂移（侧栏「安全策略」vs 库内「RLS 行级安全」）──────────────
UPDATE adh_menu_registry SET label = '安全策略' WHERE menu_key = 'data:rls';

-- ── 5. 库整洁（退役行 is_active=0，不参与渲染，仅为不遗留旧段名/并列 sort）──
UPDATE adh_menu_registry SET section = '数据资产' WHERE menu_key IN ('data:tags', 'data:glossary');
UPDATE adh_menu_registry SET sort = 34 WHERE menu_key = 'data:sync-logs';
UPDATE adh_menu_registry SET sort = 25 WHERE menu_key = 'data:quality-rules';

-- ----------------------------------------------------------------------------
-- 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- 目标库必须是 adh2：
-- SELECT DATABASE();
-- 段内 6 项按 sort 排列，label 与侧栏一致，is_active 全为 1：
-- SELECT menu_key,label,section,sort,perm_code,is_active FROM adh_menu_registry
--   WHERE module='data' ORDER BY sort;
-- 两个权限点已归位到各自菜单：
-- SELECT perm_code,label,menu_key FROM adh_perm_registry
--   WHERE perm_code IN ('metrics:read','graph:read','metrics:manage','graph:manage');
-- 模拟 analyst 的两路反查（应含 data:metrics / data:knowledge-graph）：
-- SELECT DISTINCT m.menu_key, m.label FROM adh_role_perms rp
--   JOIN adh_perm_registry p ON p.perm_code = rp.perm_code
--   JOIN adh_menu_registry m ON m.menu_key = p.menu_key AND m.is_active = 1
--   JOIN adh_roles r ON r.id = rp.role_id
--  WHERE r.name = 'analyst' AND m.module = 'data' ORDER BY m.sort;

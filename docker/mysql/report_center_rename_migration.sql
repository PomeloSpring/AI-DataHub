-- ============================================================================
-- 「报表中心」→「报告中心」术语改名（2026-11-12，用户拍板：对外文案统一成「报告」）
--
-- 为什么改：这个容器装的不是 BI 意义上的报表（定期跑出的表格），而是带结论、带证据链、
--   带版式、可导出 HTML 的**分析产物**（会话存为报告 / 定时任务产出 / 分析生成，按版本归集）。
--   资源层早就是「报告」口径（`report-template:*` 的 label 是「查看报告模板」、正文产物叫
--   HTML 报告），只有容器叫「报表中心」——同一批改动把称呼收干净，不算新增概念。
--
-- 改什么（menu-permission-sync §2：页面与角色菜单配置同批对齐）：
--   1) `adh_menu_registry` 的 workspace:reports label：报表中心 → 报告中心
--      （menu_key / perm_code / section / sort / ai_access 一律不动，存量授权继续生效）；
--   2) `adh_perm_registry` 里报字段的对外称呼：report:read/report:manage 的 label 与
--      description、report-template:manage 的 description（引用容器名）、report:read/
--      report:manage 的 ai_note（权限管理页「AI 可调用级别」说明）；
--   3) 内置报告模板正文里的骨架标题「报表标题」→「报告标题」（只碰 builtin 的 rpt_*，
--      用户「另存为模板」沉淀出的 report_template_* 属用户资产，不动）。
--
-- 不改（内部标识，不对外暴露，改了要连带重授权却零收益）：
--   menu_key `workspace:reports`、perm_code `report:read|report:manage|report-template:*`、
--   API 前缀 /api/reports*、表名 adh_reports、source_type 取值、前端文件名 ReportsCenter.tsx。
--
-- 同批的代码侧改动见 logs/patch_report_rename.py（前端报告域整文件、后端用户可见字面量定点）。
-- 幂等：全部用 REPLACE / 带守卫的 UPDATE，未命中即空更新，可重复执行。
-- 施加：mysql -h... -P... -u... -p DATABASE < 本文件
-- ============================================================================

-- ── 1. 菜单登记：只改 label，其余列不碰 ──────────────────────────────────────
UPDATE adh_menu_registry
   SET label = '报告中心'
 WHERE menu_key = 'workspace:reports' AND label <> '报告中心';

-- ── 2. 权限点对外文案（label / description / ai_note）────────────────────────
UPDATE adh_perm_registry
   SET label       = '查看报告',
       description = '报告中心查看'
 WHERE perm_code = 'report:read';

UPDATE adh_perm_registry
   SET label       = '管理报告',
       description = '报告操作'
 WHERE perm_code = 'report:manage';

-- 报告模板是「版式契约」，其描述引用容器名，随容器一起收
UPDATE adh_perm_registry
   SET description = REPLACE(description, '报表中心', '报告中心')
 WHERE perm_code IN ('report-template:read', 'report-template:manage')
   AND description LIKE '%报表中心%';

-- ai_note 归 perm_ai_access_migration 批次拥有，这里只做同义替换（不整句重写，
-- 免得用本批快照覆盖上一批登记的说明），REPLACE 未命中即原值。
UPDATE adh_perm_registry
   SET ai_note = REPLACE(ai_note, '报表', '报告')
 WHERE perm_code IN ('report:read', 'report:manage')
   AND ai_note LIKE '%报表%';

-- ── 3. 内置报告模板骨架标题（用户可见正文）───────────────────────────────────
-- 只碰 builtin（code 形如 rpt_1 / rpt_20260703001）；report_template_<时间戳> 是
-- 用户把好报告「另存为模板」沉淀的资产，其正文里的「报表」可能是真实标题，不改。
UPDATE adh_vis_components
   SET name        = REPLACE(name, '报表', '报告'),
       description = REPLACE(description, '报表', '报告'),
       style_config = REPLACE(style_config, '报表标题', '报告标题')
 WHERE category = 'report_template'
   AND code LIKE 'rpt\\_%'
   AND (name LIKE '%报表%' OR description LIKE '%报表%' OR style_config LIKE '%报表标题%');

-- ── 4. 核验（应用后人工确认，勿删）──────────────────────────────────────────
-- SELECT menu_key,label,section,module,sort,perm_code FROM adh_menu_registry
--   WHERE menu_key='workspace:reports';                 -- 期望 label='报告中心'
-- SELECT perm_code,label,description,ai_note FROM adh_perm_registry
--   WHERE perm_code IN ('report:read','report:manage','report-template:read','report-template:manage');
--   期望：不再出现「报表」；menu_key 与 ai_action_key 一字未动。
-- 全库残留检查（期望 0 行）：
-- SELECT menu_key,label FROM adh_menu_registry WHERE label LIKE '%报表%';
-- SELECT perm_code,label,description,ai_note FROM adh_perm_registry
--   WHERE label LIKE '%报表%' OR description LIKE '%报表%';
-- SELECT code,name FROM adh_vis_components
--   WHERE category='report_template' AND code LIKE 'rpt\\_%' AND style_config LIKE '%报表标题%';
-- 模拟某角色的菜单反查（应含 workspace:reports，且渲染名是「报告中心」）：
-- SELECT DISTINCT m.menu_key, m.label FROM adh_menu_registry m
--   JOIN adh_role_perms rp ON rp.perm_code = m.perm_code
--   JOIN adh_roles r ON r.id = rp.role_id
--   WHERE r.name='analyst' AND m.module='workspace' ORDER BY m.sort;

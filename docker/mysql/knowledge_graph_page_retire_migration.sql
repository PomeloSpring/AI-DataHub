-- ============================================================================
-- 「关系图谱」独立页下线（用户 2026-11-25 拍板）
--
-- 为什么下线：图谱早已是「本体建模」工作台的 graph 页签（跟随当前模型、切模型即换图），
-- 两个**派生重建动作**（图谱生成 / 同步知识库）与状态徽章（SyncBadges 30s 轮询）也已
-- 收到建模页工具栏。独立页只剩重复的模型选择器 + 一份结构化只读投影（JSON/YAML/MD
-- 与 SPARQL 查询已在建模页「文档」面板里）——一处入口在侧栏、一处在页内，人要在两处找。
--
-- 落地形态：
--   · 侧栏「数据资产」段不再有「关系图谱」；路由 /data/knowledge-graph 由前端重定向到
--     /data/ontology（兼容书签，见 App.tsx）；
--   · 权限码不变：graph:read / graph:manage 仍按原 api_pattern 裁决（建模页读图、
--     图谱重建按钮要用），只把权限点的 menu_key 从下线的菜单归到 data:ontology，
--     避免菜单下线后它们在角色页变成「无菜单归属」的孤儿码（menu-permission-sync §2.2）；
--   · 存量角色授权不变（analyst/admin 原本持有的 graph:read 继续有效，不扩权也不收权）。
--
-- 幂等：全部 UPDATE 幂等，可重复执行。
-- 不写 `USE <db>;`：库名只由连接配置决定（script-hygiene §2）。
-- ============================================================================

-- ── 1. 菜单登记：data:knowledge-graph 下线（保留行，便于回滚与审计）────────
-- 主权限码列保留原值：is_active=0 的行不参与任何反查（roles.py 两路反查都带 is_active=1），
-- 清空反而丢掉回滚线索。
UPDATE adh_menu_registry
SET is_active = 0,
    ai_access = 'none'
WHERE menu_key = 'data:knowledge-graph';

-- ── 2. 数据资产段顺序重排：去掉关系图谱后的连续 sort ──────────────────────
-- 数据集(4) → 本体建模(5) → 指标与维度(6) → 分析场景(7) → 决策建议(8)
-- 必须与侧栏 DATA_PLATFORM_MENU_ITEMS 顺序逐项一致（menu-permission-sync §2.1）
UPDATE adh_menu_registry
SET sort = CASE menu_key
        WHEN 'data:datasets'  THEN 4
        WHEN 'data:ontology'  THEN 5
        WHEN 'data:metrics'   THEN 6
        WHEN 'data:scenario'  THEN 7
        WHEN 'data:decisions' THEN 8
        ELSE sort
    END
WHERE menu_key IN ('data:datasets', 'data:ontology', 'data:metrics',
                   'data:scenario', 'data:decisions');

-- ── 3. 权限点归属：graph:read / graph:manage 挂回 data:ontology ────────────
-- 两者裁决的是建模页的读图与「图谱生成」按钮；菜单归属理应同页。
-- 只改 menu_key（sort 沿用现值，角色页菜单分组按 menu 表 sort 排，不受影响），
-- 不动 ai_access / ai_action_key / ai_note（那些列归 perm_ai_access_migration.sql 拥有）。
UPDATE adh_perm_registry
SET menu_key = 'data:ontology'
WHERE perm_code IN ('graph:read', 'graph:manage');

-- ── 4. 存量角色授权：不变（迁移前后都是既有 grant，不增不减）───────────────

-- ----------------------------------------------------------------------------
-- 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- 目标库必须是 adh2：
-- SELECT DATABASE();
-- 菜单已下线且顺序连续（预期 data:knowledge-graph is_active=0，4~8 连续无并列）：
-- SELECT menu_key,label,section,sort,is_active FROM adh_menu_registry
--   WHERE module='data' AND section='数据资产' ORDER BY sort;
-- 权限点已归到建模菜单：
-- SELECT perm_code,label,menu_key,ai_access FROM adh_perm_registry
--   WHERE perm_code IN ('graph:read','graph:manage');
-- 预期：menu_key='data:ontology'。
-- 模拟 analyst 的入口反查（应含 data:ontology，不含 data:knowledge-graph）：
-- SELECT DISTINCT m.menu_key FROM adh_role_perms rp
--   JOIN adh_perm_registry p ON p.perm_code = rp.perm_code
--   JOIN adh_menu_registry m ON m.menu_key = p.menu_key AND m.is_active = 1
--   JOIN adh_roles r ON r.id = rp.role_id
--  WHERE r.name = 'analyst' AND m.module = 'data' ORDER BY m.menu_key;
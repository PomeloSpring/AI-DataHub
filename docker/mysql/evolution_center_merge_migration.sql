-- ============================================================================
-- 进化中心并入「本体建模」页（用户 2026-11-21 拍板）
--
-- 为什么并：进化中心是**语义资产的生产与验收面**（候选 → 别名/指标/维度/术语 →
-- verified/stale），它的日常分诊入口（原「建模待办」）本来就在「本体建模」页内。
-- 两处同族却分属「页内页签」与「侧栏一级菜单」，人要在两处找同一件事；而同一批
-- pending 候选又在两处各有一套分类与处置弹窗（五个出口完全重复，口径必然漂移）。
--
-- 落地形态：进化中心整体成为「本体建模」页左侧「跨模型」分区的页签（key='evolution'），
-- 替换原「建模待办」页签；侧栏「数据资产」段不再单列「进化中心」。
--   - 页面入口 /data/ontology?tab=evolution（旧 /data/evolution 与 /system/evolution
--     由前端重定向兼容书签，见 App.tsx）；
--   - 权限码集合不变、API 裁决不变（/api/evolution* 仍按 evolution:read / evolution:manage）；
--   - 仅把两个权限点的 menu_key 归到 data:ontology，避免菜单下线后它们在角色页里
--     变成「无菜单归属」的孤儿码（menu-permission-sync §2.2）。
--
-- 幂等：INSERT ... ON DUPLICATE KEY UPDATE / UPDATE，可重复执行。
-- 不写 `USE <db>;`：库名只由连接配置决定（script-hygiene §2）。
-- ============================================================================

-- ── 1. 菜单登记：data:evolution 下线（保留行，便于回滚与审计）─────────────
UPDATE adh_menu_registry
SET is_active = 0,
    ai_access = 'none'
WHERE menu_key = 'data:evolution';

-- ── 2. 数据资产段顺序重排：去掉进化中心后的连续 sort ──────────────────────
-- 数据集(4) → 本体建模(5) → 指标与维度(6) → 关系图谱(7) → 分析场景(8) → 决策建议(9)
-- 必须与侧栏 DATA_PLATFORM_MENU_ITEMS 顺序逐项一致（menu-permission-sync §2.1）
UPDATE adh_menu_registry
SET sort = CASE menu_key
        WHEN 'data:datasets'        THEN 4
        WHEN 'data:ontology'        THEN 5
        WHEN 'data:metrics'         THEN 6
        WHEN 'data:knowledge-graph' THEN 7
        WHEN 'data:scenario'        THEN 8
        WHEN 'data:decisions'       THEN 9
        ELSE sort
    END
WHERE menu_key IN ('data:datasets', 'data:ontology', 'data:metrics',
                   'data:knowledge-graph', 'data:scenario', 'data:decisions');

-- ── 3. 权限点归属：从下线的 data:evolution 改挂 data:ontology ─────────────
-- 这两个码现在裁决的是「本体建模」页内 evolution 页签的能力，菜单归属理应同页；
-- sort 沿用 2050/2051（data 模块保留区段空档），不影响角色页菜单分组顺序。
--
-- 只改归属列（menu_key/sort）：ai_access / ai_action_key / ai_note 属
-- `evolution_ai_review_migration.sql`（AI 双闸接入）拥有的列，本迁移不得覆盖 ——
-- 两个文件按文件名顺序执行时本迁移在后，若这里写 ai_access='none' 会把已抬到
-- read/write 的 AI 通道整批关回 none（角色页能勾、工具却永不进处置面）。
UPDATE adh_perm_registry
SET menu_key = 'data:ontology',
    sort = CASE perm_code
        WHEN 'evolution:read'   THEN 2050
        WHEN 'evolution:manage' THEN 2051
        ELSE sort
    END
WHERE perm_code IN ('evolution:read', 'evolution:manage');

-- ── 4. 存量角色授权：不变（迁移前后都是仅 admin 持有，不扩权）──────────────

-- ----------------------------------------------------------------------------
-- 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- 菜单已下线且顺序连续：
-- SELECT menu_key,label,section,sort,is_active FROM adh_menu_registry
--   WHERE module='data' AND section='数据资产' ORDER BY sort;
-- 预期：data:evolution is_active=0；4~9 连续无并列。
-- 权限点已归到建模菜单：
-- SELECT perm_code,menu_key,sort,ai_access FROM adh_perm_registry
--   WHERE perm_code IN ('evolution:read','evolution:manage');
-- 预期：menu_key='data:ontology'，ai_access='none'（口径不变）。
-- 模拟 admin(role_id=1) 的入口反查：
-- SELECT DISTINCT m.menu_key FROM adh_role_perms rp
--   JOIN adh_perm_registry p ON p.perm_code = rp.perm_code
--   JOIN adh_menu_registry m ON m.menu_key = p.menu_key
--  WHERE rp.role_id = 1 AND m.is_active = 1 ORDER BY m.menu_key;
-- 预期：含 data:ontology，不含 data:evolution。
-- 建模待办独立页：菜单登记 + 权限点补齐（2026-10-28）
--
-- 背景（两件事同一批落地，遵循 menu-permission-sync）：
--   1) 「未归属资产池」从本体建模页的内嵌子视图抽为独立页 /data/asset-pool，
--      新菜单必须登记 `adh_menu_registry` 并挂上自己的权限点（角色页才渲染得出、勾得了）；
--   2) 发现两个**死开关**权限点（配了也没用 / 用了必被拒）：
--        - `metrics:manage`：evolution API 的「转指标草稿/转维度草稿」出口按它把关
--          （backend/modules/catalog/api/evolution.py），但权限表里从未登记该码
--          → get_user_role_ai_perms 走 JOIN adh_perm_registry，任何角色都拿不到
--          → 该两个出口恒定 403，从来没通过（与 ontology:publish 缺参恒 500 同类缺陷）；
--        - `evolution:read` / `evolution:manage` 已登记但未授予任何角色
--          → 「进化中心」的读取与「立即归纳」对所有人（含 admin，require_write_perm 无旁路）恒拒。
--      本迁移把 metrics:manage 登记齐、并把这三个码授予 admin 角色（修死开关，不擅自扩权）：
--      analyst 等业务角色是否放开，属业务决策，请管理员在「角色与权限」页显式勾选。
--
-- 幂等：登记用 INSERT ... ON DUPLICATE KEY UPDATE，授权用 INSERT IGNORE，可重复执行。

-- ── 1. 菜单登记（新页 data:asset-pool；同批修 label/sort 飘移）──────────
INSERT INTO adh_menu_registry (menu_key, label, section, module, perm_code, ai_access, sort, is_active)
VALUES ('data:asset-pool', '建模待办', '语义层', 'data', 'asset-pool:read', 'none', 6, 1)
ON DUPLICATE KEY UPDATE
    label = VALUES(label),
    section = VALUES(section),
    module = VALUES(module),
    ai_access = VALUES(ai_access),
    sort = VALUES(sort),
    is_active = 1;

-- label 对齐：前端侧栏与页面主标题都是「本体建模」，库里旧值「本体工作区」属文案飘移。
-- 同一批次改掉，避免角色页显示旧名（menu-permission-sync §2.1/§4）。
UPDATE adh_menu_registry SET label = '本体建模' WHERE menu_key = 'data:ontology';

-- 同段排序与侧栏顺序一致：本体建模(5) → 建模待办(6) → SQL 示例对(7)
UPDATE adh_menu_registry SET sort = 7 WHERE menu_key = 'data:sql-pairs' AND section = '语义层';

-- ── 2. 权限点登记 ───────────────────────────────────────────────
INSERT INTO adh_perm_registry
    (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note)
VALUES
    -- 新菜单的显隐反查点：菜单必须至少挂一个权限点，否则角色页渲染不出（配了也白配）
    ('asset-pool:read', '查看建模待办', 'data', '数据中台',
     '查看建模待办工作台（未归属指标/维度、未映射术语、解析回流候选）。页面数据接口仍按 metrics:read 治理，本码只管入口显隐',
     NULL, NULL, 'data:asset-pool', 71, 1, 'none', '',
     '纯人工分诊台，无 AS-BOT 工具消费该入口，不开放 AI 调用'),
    -- 修死开关：出口已在代码里按它把关，但表里从未登记 → 谁都拿不到
    ('metrics:manage', '维护指标字典', 'data', '数据中台',
     '新建/修改/删除指标与维度字典行，以及把进化候选转成指标/维度草稿',
     '/api/metrics*', 'POST,PUT,DELETE', 'data:metrics', 72, 1, 'write', '',
     '级别必须是 write 才能过 require_write_perm（写动作直执行把关口径）；本码不绑定 AS-BOT 动作键，AI 侧能力另由 ai_action_key 的功能码裁决')
ON DUPLICATE KEY UPDATE
    label = VALUES(label),
    module = VALUES(module),
    module_label = VALUES(module_label),
    description = VALUES(description),
    api_pattern = VALUES(api_pattern),
    api_method = VALUES(api_method),
    menu_key = VALUES(menu_key),
    sort = VALUES(sort),
    ai_access = VALUES(ai_access),
    ai_action_key = VALUES(ai_action_key),
    ai_note = VALUES(ai_note),
    is_active = 1;

-- 从权限点回填菜单 perm_code（幂等重跑时以权限点为真值源）
UPDATE adh_menu_registry m
  JOIN adh_perm_registry p ON p.menu_key = m.menu_key AND p.perm_code = 'asset-pool:read'
SET m.perm_code = p.perm_code
WHERE m.menu_key = 'data:asset-pool';

-- ── 3. 存量角色默认授权 ─────────────────────────────────────────
-- 3.1 建模待办入口：迁移前它嵌在本体建模页里，能看到本体建模的人就能看。
--     抽成独立页后，这批人的入口不能凭空消失 → 给已持有 metrics:read 的角色默认授权
--     （页面主数据接口 /api/metrics/asset-pool 正是按 metrics:read 治理，
--      两者同集才不会配出“点得开却读不到”的空承诺；管理员仍可按角色收回）。
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT rp.role_id, 'asset-pool:read', NOW()
FROM adh_role_perms rp
WHERE rp.perm_code = 'metrics:read';

-- 3.2 修死开关只放开到 admin：这两个出口此前对**所有人**都拒绝（含 admin），
--     给 admin 授予是恢复“功能本来看起来该能用”的现状；analyst 等一律不动，
--     写权限的扩散必须由人在角色页决定，不由迁移擅自放大。
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT r.id, p.perm_code, NOW()
FROM adh_roles r
CROSS JOIN (
    SELECT 'metrics:manage' AS perm_code
    UNION ALL SELECT 'evolution:read'
    UNION ALL SELECT 'evolution:manage'
) p
WHERE r.name = 'admin' AND r.is_active = 1;

-- ── 4. 核验（应用后人工确认，勿删）──────────────────────────────
-- SELECT menu_key,label,section,perm_code,sort FROM adh_menu_registry
--   WHERE menu_key IN ('data:ontology','data:asset-pool') ORDER BY sort;
-- SELECT perm_code,label,menu_key,ai_access,api_pattern,api_method FROM adh_perm_registry
--   WHERE perm_code IN ('asset-pool:read','metrics:manage','evolution:read','evolution:manage');
-- SELECT r.name, rp.perm_code FROM adh_roles r JOIN adh_role_perms rp ON rp.role_id=r.id
--   WHERE rp.perm_code IN ('asset-pool:read','metrics:manage') ORDER BY r.name;
-- 模拟 analyst 的菜单反查（应含 data:asset-pool，前提是它有 metrics:read）：
-- SELECT DISTINCT m.menu_key FROM adh_menu_registry m
--   JOIN adh_role_perms rp ON rp.perm_code = m.perm_code
--   JOIN adh_roles r ON r.id = rp.role_id
--   WHERE r.name='analyst' AND m.menu_key LIKE 'data:%' ORDER BY m.menu_key;

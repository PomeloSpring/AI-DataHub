-- 本体写权限化：新增对象 / 发布 两个权限点 + 草案/发布两段式（2026-10-26 用户拍板）
--
-- 背景：废除「AS-BOT 不得写业务本体」的硬禁令，改为按权限码裁决。
--   ontology:create  新增对象（AS-BOT 通道，用户 UI 不提供修改入口）
--   ontology:save    修改对象/属性/关系（复用现有码，落草案）
--   ontology:publish 发布草案 → 级联（重展开 objects / 重建图谱 / 同步知识库）
--                     发布前须在 chat 中用 ask_user 与用户确认
--
-- 幂等：可重复执行。登记用 ON DUPLICATE KEY UPDATE，授权用 INSERT IGNORE。
-- 遵循 menu-permission-sync §4：登记 + 归属 + 存量角色默认授权 + 回填 perm_code 同批落地。

-- ── 1. 权限点登记 ───────────────────────────────────────────────
-- menu_key 归 data:ontology（读权限归消费菜单，写权限同归属：本体建模页的写动作）
INSERT INTO adh_perm_registry
    (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note)
VALUES
    ('ontology:create', '新增本体对象', 'data', '数据',
     '在本体模型中新增对象（只增不删；用户 UI 仅此一个编辑入口，修改走 AS-BOT）',
     '/api/ontology/models/*/objects', 'POST', 'data:ontology', 61, 1, 'write',
     'ontology.add_objects',
     'AI 权限：AS-BOT 新增对象。改动落草案，需 ontology:publish 发布才生效'),
    ('ontology:publish', '发布本体草案', 'data', '数据',
     '把本体草案提升为生效版本，并级联重展开对象/重建图谱/同步知识库',
     '/api/ontology/models/*/publish', 'POST', 'data:ontology', 63, 1, 'write',
     'ontology.publish_model',
     'AI 权限：发布前必须在 chat 中用 ask_user 向用户确认；发布才级联生效')
ON DUPLICATE KEY UPDATE
    label = VALUES(label),
    module = VALUES(module),
    module_label = VALUES(module_label),
    description = VALUES(description),
    api_pattern = VALUES(api_pattern),
    api_method = VALUES(api_method),
    menu_key = VALUES(menu_key),
    ai_access = VALUES(ai_access),
    ai_action_key = VALUES(ai_action_key),
    ai_note = VALUES(ai_note),
    is_active = 1;

-- 从权限点回填 menu_registry.perm_code（菜单显隐按权限码反查用）
UPDATE adh_menu_registry m
  JOIN adh_perm_registry p ON p.menu_key = m.menu_key
SET m.perm_code = p.perm_code
WHERE m.menu_key = 'data:ontology' AND (m.perm_code IS NULL OR m.perm_code = '');

-- ── 2. 存量角色默认授权 ─────────────────────────────────────────
-- 漏这步 = 老用户入口凭空消失（menu-permission-sync §2.3）。
-- 用户拍板：admin + 数据开发 两个角色。
--
-- ⚠ 待确认：库里当前没有名为「数据开发」的角色（只有 admin / analyst(数据分析师) /
--   viewer / Operations Manager）。写权限不猜，第二条先按 analyst 落，若「数据开发」
--   是新角色或另有所指，请改下面的 name 后重跑（幂等）。
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT r.id, p.perm_code, NOW()
FROM adh_roles r
JOIN adh_perm_registry p ON p.perm_code IN ('ontology:create', 'ontology:publish')
WHERE r.name IN ('admin', 'analyst');

-- 复用现有 ontology:save：确保这两个角色同样具备（修改走 AS-BOT 的前提）
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT r.id, 'ontology:save', NOW()
FROM adh_roles r
WHERE r.name IN ('admin', 'analyst');

-- ── 3. 核验（人工执行后确认，勿删）─────────────────────────────
-- SELECT p.perm_code, p.label, p.ai_access, p.menu_key FROM adh_perm_registry p
--   WHERE p.perm_code IN ('ontology:create','ontology:save','ontology:publish');
-- SELECT r.name, rp.perm_code FROM adh_roles r JOIN adh_role_perms rp ON rp.role_id = r.id
--   WHERE rp.perm_code IN ('ontology:create','ontology:save','ontology:publish') ORDER BY r.name;

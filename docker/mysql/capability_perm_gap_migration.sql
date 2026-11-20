-- ============================================================================
-- 能力↔权限码补口：把「有页面/有接口但拿不到码」的能力补上真码
--
-- 背景（2026-11-20 用 openapi 全路由 × adh_perm_registry 逐条 fnmatch 实探）：
--   下列接口此前不匹配任何权限码，中间件 fail-closed（403）→ 除 admin 旁路外谁都调不动，
--   而菜单/页面又已经存在（给了入口却给不了能力）：
--     1. GET  /api/tags*、/api/tags/categories、/api/tags/{id}/values：标签管理页读面无码
--        （tags:manage 只写 POST/PUT/DELETE，读被漏掉）；
--     2. GET/PUT/DELETE /api/playground/queries*：Playground「保存查询/查询数据集」无码
--        （playground:read 的 api_pattern 是空串，只有 POST 被 playground:execute 兜住）；
--     3. POST /api/lineage/{nodes,edges,parse-sql}：手工登记血缘无码（lineage:read 只 GET）；
--     4. GET/DELETE /api/embed/applications*、POST/PUT /api/embed/applications/{id}、
--        GET /api/embed/logs：集成应用（AK 嵌入）整域无码；
--     5. PATCH /api/scheduled-tasks/tasks/{id}/toggle、PATCH /api/sync/tasks/{id}/toggle：
--        开关动作是 PATCH，而两个 manage 码的方法集只有 POST,PUT,DELETE → 停用/启用恒 403。
--
-- 另有两类**不是缺码**，不要在这里补（已写进系统本体规则 perm_no_code_is_admin_only /
-- perm_self_service_owner_only，避免下次又按"没码"来配码）：
--   * 路由层 require_admin 把门的：/api/admin/terms*、/api/admin/metadata*、/api/admin/table-info*、
--     /api/admin/relations* 写面、/api/admin/sync*、/api/admin/execution-layers*、/api/admin/release-notes*、
--     /api/task-monitor*、/api/model-train*；补码也不会放行非管理员，要开须先改路由依赖（另议）。
--   * 个人自助（中间件 _SELF_ROUTE_PATTERNS 属主校验）：/api/assets*、/api/release-notes* 读、
--     /api/memory*、/api/workspace-assets/*、/api/chat/session-file*；域码会与属主校验重复裁决。
--
-- 幂等：INSERT ... ON DUPLICATE KEY UPDATE + UPDATE 带条件 + INSERT IGNORE 授权；
-- 可重复执行，不含 USE（库由连接配置决定）。
-- ============================================================================

-- 1. 标签读面：GET /api/tags*（标签页读清单/分类/取值）
INSERT INTO adh_perm_registry
    (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note)
VALUES
    ('tags:read', '查看标签', 'data', '数据中台',
     '查看标签清单/分类与标签取值（标签管理与本体工作区读面）',
     '/api/tags*', 'GET', 'data:ontology', 2046, 1, 'none', '',
     '标签是人工治理词表，无 AS-BOT 标签工具消费该入口，不开放 AI 调用')
ON DUPLICATE KEY UPDATE
    api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
    menu_key = VALUES(menu_key), is_active = 1, ai_access = VALUES(ai_access),
    ai_action_key = '', ai_note = VALUES(ai_note);

-- 2. 已保存查询写面：PUT/DELETE /api/playground/queries*（保存查询/查询数据集归个人复用资产）
INSERT INTO adh_perm_registry
    (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note)
VALUES
    ('playground:manage', '维护已保存查询', 'data', '数据中台',
     '修改/删除自己在 Playground 保存的查询与查询数据集（读取归 playground:read）',
     '/api/playground/queries*', 'PUT,DELETE', 'data:playground', 2023, 1, 'none', '',
     '已保存查询是个人复用资产，SQL 正文属敏感信息，不进入 AI 工具面')
ON DUPLICATE KEY UPDATE
    api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
    menu_key = VALUES(menu_key), is_active = 1, ai_access = VALUES(ai_access),
    ai_action_key = '', ai_note = VALUES(ai_note);

-- 3. 血缘写面：手工登记节点/边、SQL 血缘解析
INSERT INTO adh_perm_registry
    (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note)
VALUES
    ('lineage:manage', '维护数据血缘', 'data', '数据中台',
     '手工登记血缘节点/边与 SQL 血缘解析（血缘查看归 lineage:read）',
     '/api/lineage*', 'POST', 'data:lineage', 2082, 1, 'none', '',
     '血缘写面改变下游影响分析口径，属人工治理动作，不开放给 AI')
ON DUPLICATE KEY UPDATE
    api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
    menu_key = VALUES(menu_key), is_active = 1, ai_access = VALUES(ai_access),
    ai_action_key = '', ai_note = VALUES(ai_note);

-- 4. 集成应用（AK 嵌入）：/api/embed/applications*、/api/embed/logs
--    注意：这里只登记管理面；嵌入运行入口（AK 鉴权）不在这些路径上。
INSERT INTO adh_perm_registry
    (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note)
VALUES
    ('embed:read', '查看集成应用', 'system', '系统配置',
     '查看嵌入应用清单与调用日志（应用密钥只在创建/轮换时一次性返回）',
     '/api/embed/applications*,/api/embed/logs', 'GET', 'system:settings', 1203, 1, 'none', '',
     '嵌入应用配置含访问凭据语义，不开放 AI 调用'),
    ('embed:manage', '管理集成应用', 'system', '系统配置',
     '新建/修改/删除嵌入应用（开启对话、看板表白名单、限流、AK 轮换）',
     '/api/embed/applications*', 'POST,PUT,DELETE', 'system:settings', 1204, 1, 'none', '',
     '应用密钥属凭据，AI 侧任何通道都不得代建/代改/代读')
ON DUPLICATE KEY UPDATE
    label = VALUES(label), description = VALUES(description),
    api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
    menu_key = VALUES(menu_key), is_active = 1, ai_access = VALUES(ai_access),
    ai_action_key = '', ai_note = VALUES(ai_note);

-- 5. 修已存在码的漏项 -----------------------------------------------------------

-- 5.1 playground:read 原本 api_pattern 为空（只有入口显隐），读已保存查询没有落点
UPDATE adh_perm_registry
   SET api_pattern = '/api/playground/queries*', api_method = 'GET'
 WHERE perm_code = 'playground:read' AND (api_pattern IS NULL OR api_pattern = '');

-- 5.2 任务调度/数据同步的启停是 PATCH，原方法集漏了它（PATCH 恒 403）
UPDATE adh_perm_registry SET api_method = 'POST,PUT,PATCH,DELETE' WHERE perm_code = 'scheduled:manage';
UPDATE adh_perm_registry SET api_method = 'POST,PUT,PATCH,DELETE' WHERE perm_code = 'sync:manage';

-- 5.3 术语写面：路由层 require_admin 已把门，本码只登记读；此处补充说明避免被当成"漏配写码"
UPDATE adh_perm_registry
   SET ai_note = '术语读面；写面 POST/PUT/DELETE /api/admin/terms* 由路由层 require_admin 把关（尚无独立管理码）'
 WHERE perm_code = 'glossary:read';

-- 6. 存量角色默认授权（维持迁移前的入口可见现状：谁本来就持有同域码，就给谁补上）
--    新码若不授权给既有角色，角色页会出现"该码存在但没人有"，而且原页面继续 403。
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT DISTINCT rp.role_id, 'tags:read', NOW()
  FROM adh_role_perms rp WHERE rp.perm_code = 'tags:manage';

INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT DISTINCT rp.role_id, 'playground:manage', NOW()
  FROM adh_role_perms rp WHERE rp.perm_code = 'playground:execute';

INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT DISTINCT rp.role_id, 'lineage:manage', NOW()
  FROM adh_role_perms rp WHERE rp.perm_code = 'lineage:read';

-- 集成应用是系统配置面，迁移前仅 admin 可达 → 只授权管理员角色（不扩大到既有业务角色）
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT id, 'embed:read', NOW() FROM adh_roles WHERE name = 'admin' AND is_active = 1;
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT id, 'embed:manage', NOW() FROM adh_roles WHERE name = 'admin' AND is_active = 1;

-- 7. 标签管理菜单：显隐反查点改挂读码（原挂 tags:manage，只有写权限的人才能看见读页面）
UPDATE adh_menu_registry SET perm_code = 'tags:read' WHERE menu_key = 'data:tags';
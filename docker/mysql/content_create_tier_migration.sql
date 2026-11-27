-- ============================================================================
-- 内容中心权限分档：新增「创作」档（article:create），并把「只读」档移入内容中心块
--
-- 背景（用户拍板，计划「内容中心权限分档」）：
--   角色页此前只有整包的 article:manage（新建+编辑+删除+批量+栏目标签+提升），
--   没有「只写草稿 + 提交审核」的普通档；article:read（只读）的功能项挂在
--   home:feed（module=home），在角色页落到一个重名「内容中心」的孤立分组里。
--
--   技术根因：adh_perm_registry 一行的 api_pattern × api_method 是笛卡尔积，
--   无法表达「允许 POST 新建、不允许 POST 批量删除」。解法是**方法限定条目**：
--   api_pattern 里形如 `METHOD path` 的条目（含空格）由中间件按该 method 精确
--   登记、不与本行 api_method 求积（见 backend/common/api_permission.py 规则 7）。
--   纯路径条目行为完全不变，本迁移不动任何既有接口路径与方法。
--
-- 档位层级：只读(article:read) ⊂ 创作(article:create) ⊂ 管理(article:manage)；
-- 审核(article:review)/发布(article:publish)/同步(kbdoc:write) 独立高权限，不放宽。
-- 每档自足：勾单个档位页面即可用，无需级联勾选也能工作（级联只是角色页的保险）。
--
-- 三条刻意配置（不是漏做）：
--   * article:create 的 ai_access='none' + ai_note 写明「AI 内容工具授权来源仍是
--     article:manage（管理档），创作档暂不单独接 AI」—— 不造空承诺；6 个 content_*
--     动作不迁（tests/test_content_ai_access.py 拦）。
--   * 默认授予**只补现状**：持 article:manage 的角色 INSERT IGNORE 补 article:create
--     （保住「我的草稿」入口不回归）；viewer/analyst 不动 —— 不扩大默认授权。
--   * 提升通道（promotions*）、删除/批量删除、栏目标签写、reviews*、publish* 一律
--     **不含**在 article:create 的 pattern 里（用例逐条断言恰好）。
-- ============================================================================

USE adh2;

-- ----------------------------------------------------------------------------
-- 1) 权限点登记：article:create（创作档 —— 只写草稿 + 提交审核）
--    api_pattern 全部是方法限定条目（METHOD path，含空格）：
--      * GET  三条前缀  → 自带只读（每档自足，无需再勾 article:read）
--      * POST /api/content/articles → 精确新建；**不含** batch-delete（同为 POST、
--        同在 /articles* 前缀下，笛卡尔积放行正是要堵的洞）
--      * PUT  /api/content/articles* → 编辑（head revision，不触线上）
--      * POST …/submit、…/batch-submit → 提交审核
--      * POST …/files、DELETE …/files/* → 附件上传/删除
--      * 不含：DELETE /api/content/articles*（单条删除）、batch-delete、
--        categories/tags 写、promotions*、reviews*、publish*
--    api_method='GET,POST,PUT'：限定条目不消费该字段（不与之求积），保留值供
--    既有菜单 perm_code 回填语句的 FIND_IN_SET('GET', …) 使用。
--    sort=811：与管理档同组相邻展示（两者分属不同菜单，不冲突）。
-- ----------------------------------------------------------------------------
INSERT INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note) VALUES
  ('article:create', '编写草稿并提交审核', 'content', '内容中心',
   '只写草稿：新建/编辑自己的内容条目、传附件、提交审核。不能删除（单条与批量都不能）、不能动栏目标签、不能提升、不能审核/发布/同步',
   'GET /api/content/articles*,GET /api/content/categories*,GET /api/content/tags*,POST /api/content/articles,PUT /api/content/articles*,POST /api/content/articles/*/submit,POST /api/content/articles/batch-submit,POST /api/content/articles/*/files,DELETE /api/content/articles/*/files/*',
   'GET,POST,PUT',
   'content:drafts', 811, 1, 'none', '',
   'AI 内容工具授权来源仍是 article:manage（管理档），创作档暂不单独接 AI：本码 ai_action_key 留空、不挂任何 content_* 动作，配出级别也没有消费方（空承诺）。要在 Chat 里让 AI 写草稿，勾管理档')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), module = VALUES(module), module_label = VALUES(module_label),
  description = VALUES(description), api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
  menu_key = VALUES(menu_key), sort = VALUES(sort), ai_access = VALUES(ai_access),
  ai_action_key = VALUES(ai_action_key), ai_note = VALUES(ai_note), is_active = 1;

-- ----------------------------------------------------------------------------
-- 2) 只读档移入内容中心块：article:read.menu_key home:feed → content:manage
--    （修掉角色页两个重名「内容中心」分组：功能项不再落 module=home 桶）
--    首页资讯区显隐不受影响：那条链路是菜单行 home:feed.perm_code='article:read'
--    反查 adh_role_perms，与本行 menu_key 无关（tests/test_home_feed_access.py 锁）。
--    副作用（有意）：只读角色经 perm_registry.menu_key 归属反查获得「内容管理」
--    菜单 = 只读浏览入口。
-- ----------------------------------------------------------------------------
UPDATE adh_perm_registry SET menu_key = 'content:manage'
WHERE perm_code = 'article:read';

-- ----------------------------------------------------------------------------
-- 3) 菜单 perm_code 显式收敛（双保险）
--    既有回填语句是「多码菜单 last-writer-wins」：JOIN p.menu_key=m.menu_key 时
--    content:drafts 会同时命中 article:manage 与 article:create、content:manage 会
--    同时命中 article:manage 与 article:read（§2 之后），写入哪行取决于执行顺序。
--    这里显式钉死，之后重跑任何回填语句都由本节口径收敛：
--      content:drafts  → article:create（创作档入口）
--      content:manage  → article:manage（管理档入口；只读角色靠归属反查，不靠主码）
-- ----------------------------------------------------------------------------
UPDATE adh_menu_registry SET perm_code = 'article:create'
WHERE menu_key = 'content:drafts';
UPDATE adh_menu_registry SET perm_code = 'article:manage'
WHERE menu_key = 'content:manage';

-- ----------------------------------------------------------------------------
-- 4) 存量角色默认授权：**只补现状，不扩大**
--    持 article:manage 的角色补 article:create —— 管理档是超集，补授只为保住
--    「我的草稿」菜单入口（菜单主码改挂 create 后，只持 manage 的角色经主码
--    反查会丢入口 = 回归缺陷）。
--    viewer/analyst 只持 article:read，**不动**（不给它们新增写能力）。
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO adh_role_perms (role_id, perm_code)
SELECT rp.role_id, 'article:create'
FROM adh_role_perms rp
WHERE rp.perm_code = 'article:manage';

-- ----------------------------------------------------------------------------
-- 5) 核验（应用后人工确认，勿删）
-- ----------------------------------------------------------------------------
-- 5.1 registry 行（应见 article:create 与 article:read 两行的最新归属）：
-- SELECT perm_code, label, menu_key, api_pattern, api_method, ai_access, ai_action_key
--   FROM adh_perm_registry
--   WHERE perm_code IN ('article:create','article:read','article:manage') ORDER BY sort;
-- 5.2 角色授予（持 manage 的角色应各多一行 article:create；viewer/analyst 仍只有 read）：
-- SELECT r.name, rp.perm_code FROM adh_roles r JOIN adh_role_perms rp ON rp.role_id=r.id
--   WHERE rp.perm_code LIKE 'article:%' ORDER BY r.name, rp.perm_code;
-- 5.3 菜单主码（content:drafts 应为 article:create）：
-- SELECT menu_key, perm_code FROM adh_menu_registry
--   WHERE menu_key IN ('content:drafts','content:manage','home:feed') ORDER BY sort;
-- 5.4 模拟角色菜单反查（与 /roles/current/permissions 同口径：主码 ∪ 归属码）：
--   -- 只读角色（viewer）应见 content:manage + home:feed，不见 content:drafts：
-- SELECT DISTINCT m.menu_key FROM adh_menu_registry m
--   JOIN adh_role_perms rp ON rp.perm_code = m.perm_code
--  UNION
-- SELECT DISTINCT m.menu_key FROM adh_menu_registry m
--   JOIN adh_perm_registry p ON p.menu_key = m.menu_key
--   JOIN adh_role_perms rp ON rp.perm_code = p.perm_code
--   WHERE rp.role_id = (SELECT id FROM adh_roles WHERE name='viewer');
--   -- 创作角色（仅 article:create）应见 content:drafts，不见 reviews/publish 类入口：
--   （同上两条，role 换成只持 article:create 的角色）

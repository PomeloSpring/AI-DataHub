-- ============================================================================
-- 首页资讯区（把首页用起来，不单独建资讯模块）
--
-- 背景（用户 2026-11-06 拍板）：原计划里面向全员消费的「资讯门户」是独立模块，
-- 本轮改为**直接把它放进首页** —— 首页左栏就是资讯流 + 一个 `/read/:slug` 阅读页。
-- 因此本迁移补的就是"门户"那一批欠下的两件事（上一批 content_center_migration.sql
-- 的注释里明确写了它们属门户模块、不提前登记，现在门户有页面有端点了，正是同批登记的时机）:
--
--   1. 读者权限码 `article:read`：首页资讯区要对**所有登录用户**可用。
--      上一批把内容读接口挂在 `article:manage` 的 GET 上（"能管就能看"），
--      那是编排模块内部的口径；对读者的可见性必须由读者自己的码裁决，
--      否则管理员收不回首页资讯区，且会出现"门户读走编辑权"的第二套含义。
--      **不进 `_OPEN_READ_ROUTES` 白名单**（与既有范式一致：全员可读也要走权限码 +
--      存量角色默认授权，保持权限裁决点唯一）。
--
--   2. 菜单登记 `home:feed`：菜单显隐的真值源是 `adh_menu_registry.perm_code` ⋈
--      `adh_role_perms`（menu-permission-sync §3），所以 `article:read` 必须挂在一条
--      菜单上，否则角色页渲染不出、勾了也白配。首页本身恒可见（模块切换第一项），
--      本条菜单管的是**首页里的资讯区**这一块。
--
-- 三条刻意配置（不是漏做）：
--   * `article:read` 的 `ai_access='none'`：AI 侧读内容走的是 `article:manage` 下已登记的
--     `content_article.list` / `content_article.get` 动作，本码只裁决"人能不能在首页看到资讯区"。
--     把它配成 read 会造出"配了级别却无消费方"的空承诺（tests/test_function_ai_access.py 拦）。
--   * `article:manage` 的 `api_method` **保持含 GET 不动**：两个码各自都带 GET 同一批 pattern，
--     中间件是"任一已授权码命中即放行"，所以只勾编辑权的角色照常加载管理清单（零回归）。
--   * 存量角色**全部**默认授 `article:read`：维持"首页给全员用"的现状；管理员可按角色收回。
--     注意只授 read，不授 manage/review/publish（写能力不扩权，与上一批同口径）。
--
-- 附件下载的越权缺口由代码修（不在本迁移）：`/api/content/articles*` 的 fnmatch 覆盖不到
-- "只匹配列表不匹配子路径"，所以读者码一旦生效就连带覆盖 `…/files/{id}/download`。
-- 服务端 `content_service.file_for_download(..., allow_draft=)` 必须自己判发布状态，
-- 否则草稿附件对全员可下。见 backend/modules/platform/api/content.py 同批改动。
-- ============================================================================

USE adh2;

-- ----------------------------------------------------------------------------
-- 1) 权限点登记：article:read（面向读者的门户消费权）
--    pattern 与 content.py 的只读端点逐条对应；method 只有 GET（写能力一条都不给）
--
--    【纪律】本批**不重写** article:manage 任何字段，所以它不得出现在这里的 VALUES 清单里。
--    曾经带过它并写 ai_action_key=''，ON DUPLICATE 就按本行的空串覆盖了上一批
--    content_ai_action_migration.sql 回填的 6 个内容动作（双侧一致的 DB 侧真值），
--    等于静默拆掉了 Chat 里的内容创作工具 —— 幂等迁移只能动自己拥有的列。
--    两个码各自都带 GET 同一批 pattern 是刻意的：中间件是“任一已授权码命中即放行”，
--    所以只勾编辑权的角色照常加载管理清单（零回归），无需在此声明。
-- ----------------------------------------------------------------------------
INSERT INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note) VALUES
('article:read', '阅读已发布内容', 'content', '内容中心',
   '首页资讯区与文章阅读页的可见性：只消费已发布（live）内容，看不到草稿、不能写。草稿/待审只随编辑权与审核权出现',
   '/api/content/articles*,/api/content/categories*,/api/content/tags*', 'GET',
   'home:feed', 810, 1, 'none', '',
   '本码只裁决“人在首页能不能看到资讯区”，不接 AI 工具：AI 读内容走 article:manage 下的 content_article.list / content_article.get（已在该码 ai_action_key 登记）。把本码配成只读可见不会多出任何 AI 能力')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), module = VALUES(module), module_label = VALUES(module_label),
  description = VALUES(description), api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
  menu_key = VALUES(menu_key), sort = VALUES(sort), is_active = 1,
  ai_access = VALUES(ai_access), ai_note = VALUES(ai_note);

-- ----------------------------------------------------------------------------
-- 2) 菜单登记：home:feed（首页资讯区）
--    module='home' 是新模块桶：角色页的分桶从接口数据推导、未登记模块排最后但不丢
--    （RoleManagement.tsx MODULE_ORDER 注释），显示名取本行的 module_label，不会渲染成裸 key。
--    section 用「首页」而非空串：与其它模块的分组口径一致，角色页按段展示。
-- ----------------------------------------------------------------------------
INSERT INTO adh_menu_registry (menu_key, label, section, module, sort, perm_code, ai_access, is_active) VALUES
('home:feed', '首页资讯区', '首页', 'home', 10, 'article:read', 'none', 1)
ON DUPLICATE KEY UPDATE
  label = VALUES(label), section = VALUES(section), module = VALUES(module),
  sort = VALUES(sort), perm_code = VALUES(perm_code), ai_access = VALUES(ai_access), is_active = 1;

-- ----------------------------------------------------------------------------
-- 3) 存量角色默认授权：所有角色都拿到"能看首页资讯"（幂等，可按角色收回）
--    这一步漏了 = 上线后非 admin 角色首页资讯区直接消失（入口凭空不见，属回归缺陷）
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO adh_role_perms (role_id, perm_code)
SELECT r.id, 'article:read' FROM adh_roles r;

-- ----------------------------------------------------------------------------
-- 4) 核验（人工执行，确认迁移生效）
-- ----------------------------------------------------------------------------
-- 权限点与菜单归属：
-- SELECT perm_code, label, api_pattern, api_method, menu_key, ai_access FROM adh_perm_registry
--   WHERE perm_code IN ('article:read','article:manage') ORDER BY sort;
-- 菜单登记：
-- SELECT menu_key, label, section, module, sort, perm_code FROM adh_menu_registry
--   WHERE menu_key='home:feed';
-- 各角色是否都拿到了读权（应 4 行，缺行=默认授权没生效）：
-- SELECT r.name, rp.perm_code FROM adh_roles r JOIN adh_role_perms rp
--   ON rp.role_id=r.id AND rp.perm_code='article:read' ORDER BY r.name;
-- 模拟 viewer 角色的首页资讯区反查（应有 home:feed）：
-- SELECT m.menu_key FROM adh_menu_registry m JOIN adh_role_perms rp
--   ON rp.perm_code=m.perm_code AND rp.role_id=(SELECT id FROM adh_roles WHERE name='viewer')
--   WHERE m.is_active=1 AND m.module='home';

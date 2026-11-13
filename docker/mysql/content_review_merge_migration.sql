-- ============================================================================
-- 内容中心 IA 收敛：「审核发布」并入「内容管理」（2026-11-13 拍板）
--
-- 背景：审核/发布过去单开一页（/content/review），与「内容管理」看的是同一批条目，
-- 差别只在状态。用户拍板「没必要单独一个页面，就在内容管理中就行，只不过是状态的区别」，
-- 同时「未提交的草稿不该出现在内容管理里」（草稿属个人工作稿，只在「我的草稿」可见）。
--
-- 本文件只做配置面登记（menu-permission-sync §2 五件事），代码侧同批落地：
--   1) 菜单登记：content:review 退役（is_active=0，**保留行**以留审计与防未来重复登记）；
--   2) 权限点归属：article:review / article:publish 的 menu_key → content:manage
--      （审核与发布现在都发生在内容管理的行操作上；归属不跟着走，角色页就会
--       继续按旧菜单分组渲染 = 「配了没用」的空承诺）；
--   3) 存量角色授权：本批**不新增权限码**，既有 adh_role_perms 原样有效；
--      只持有 article:review（没有 article:manage）的审核人角色仍能看见「内容管理」，
--      由 backend/modules/auth/api/roles.py 的菜单反查改造保证
--      （并集：menu_registry.perm_code 命中 ∪ perm_registry.menu_key 归属命中）；
--   4) 前端显隐口径：ContentLayout 去掉 /content/review 导航项；旧路由重定向到
--      /content/manage?status=in_review（书签与外部链接不失效）；
--   5) 回归：tests/test_content_review_merge.py（本批配置 + 反查 + 草稿可见性）。
--
-- 幂等：全部 UPDATE / 条件写，可重复执行。
-- ============================================================================

-- 1) 菜单退役：不再作为可勾选的菜单项出现在角色权限页「菜单与功能」
UPDATE adh_menu_registry
   SET is_active = 0
 WHERE menu_key = 'content:review';

-- 2) 权限点归属迁移：审核与发布挂到内容管理
--    label 不动（动作语义没变），description 里补上新的发生位置，避免管理员照旧文案找不到入口
UPDATE adh_perm_registry
   SET menu_key = 'content:manage',
       description = '在「内容管理」筛出待审内容，逐条确认门禁提醒后通过或驳回'
                     '（驳回必填原因；作者不得自审自己的稿，admin 例外且留审计）'
 WHERE perm_code = 'article:review';

UPDATE adh_perm_registry
   SET menu_key = 'content:manage',
       description = '把审核通过的版本置为线上可见（或免审直发，须写明原因入审计），'
                     '以及下架。发布对全体登录用户可见，不受数据权限过滤。入口在「内容管理」行上'
 WHERE perm_code = 'article:publish';

-- 2b) 内容管理菜单的主权限码仍是 article:manage（显隐反查的主口径不变）；
--     审核/发布靠上面 menu_key 归属进入同一菜单的并集反查，不改动 perm_code，
--     避免把「能审核」误当成「能编草稿」。
UPDATE adh_menu_registry
   SET perm_code = 'article:manage',
       label = '内容管理',
       section = '内容生产',
       sort = 20,
       is_active = 1
 WHERE menu_key = 'content:manage';

-- 3) 我的草稿：口径不变（工作稿只在个人区），但登记说明要点明它是草稿的唯一去处，
--    否则后来人会把「内容管理看不到草稿」当成漏配而回头去放开。
UPDATE adh_menu_registry
   SET label = '我的草稿',
       section = '我的工作台',
       sort = 10,
       perm_code = 'article:manage',
       is_active = 1
 WHERE menu_key = 'content:drafts';

-- 核验（人工执行，期望：content:review 的 is_active=0；
--        article:review / article:publish 的 menu_key='content:manage'）:
--   SELECT menu_key, label, is_active FROM adh_menu_registry WHERE module='content' ORDER BY sort;
--   SELECT perm_code, menu_key, ai_access FROM adh_perm_registry WHERE perm_code LIKE 'article:%';
--   -- 模拟"只有审核权"的角色反查（应含 content:manage，不含 content:review）:
--   SELECT r.name FROM adh_roles r
--     JOIN adh_role_perms rp ON rp.role_id=r.id
--    WHERE rp.perm_code='article:review';

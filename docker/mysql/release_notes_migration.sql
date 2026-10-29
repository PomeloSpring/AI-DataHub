-- ============================================================================
-- 新手引导 + 版本更新公告 — 建表与菜单/权限登记（幂等，可重复执行）
--
-- 背景:
--   * 版本公告内容落 adh_release_notes, 管理员在「系统配置 → 版本公告」编辑,
--     发一条公告不需要重新构建前端(与"前端硬编码 changelog"相对);
--   * 引导/已读状态落 adh_user_guide_state(按 user_id + state_key), 换浏览器或
--     清缓存不会重看, 发一条 is_major=1 的公告即可对全体用户重触发引导;
--   * 引导步骤定义**不落库**(事实源在前端 lib/onboarding/guides.ts, 与 DOM 的
--     data-tour 锚点同批演进), 库里只存公告与用户状态, 避免库里 selector 与
--     前端代码版本错配后引导点不到元素。
--
-- 状态 key 口径(与 backend/modules/platform/api/release_notes.py 的白名单一致):
--   guide:<guideId>  引导完成/跳过(completed/skipped), 特殊 id: all=全局静音、
--                    welcome=欢迎总览
--   note:<version>   公告已读(read)
--
-- 执行: mysql -h <host> -P <port> -u root -p < release_notes_migration.sql
-- ============================================================================
USE adh2;

-- ----------------------------------------------------------------------------
-- 1) 版本公告
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_release_notes (
  id           BIGINT       NOT NULL AUTO_INCREMENT,
  version      VARCHAR(32)  NOT NULL COMMENT '版本号(唯一), 如 1.1.0',
  title        VARCHAR(200) NOT NULL COMMENT '版本标题(业务可读)',
  summary      VARCHAR(500) NOT NULL DEFAULT '' COMMENT '一句话摘要',
  sections     JSON         NULL COMMENT '分组条目 [{kind:feature|improvement|fix, items:[str]}]',
  guide_key    VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '关联引导 id(前端 GUIDES 的键), 空=无引导',
  is_major     TINYINT      NOT NULL DEFAULT 0 COMMENT '1=重大版本, 未读时对全体用户重触发引导',
  is_published TINYINT      NOT NULL DEFAULT 1 COMMENT '1=对用户可见',
  published_at DATETIME     NULL COMMENT '生效时间(列表按此倒序)',
  updated_by   VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '最后编辑者用户名',
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_release_version (version),
  KEY idx_published_at (is_published, published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='版本更新公告(管理员发布, 顶栏公告按钮消费)';

-- ----------------------------------------------------------------------------
-- 2) 用户引导/公告已读状态
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_user_guide_state (
  user_id    BIGINT      NOT NULL COMMENT '属主 (adh_users.id) — 跨工作空间共享',
  state_key  VARCHAR(96) NOT NULL COMMENT 'guide:<guideId> 或 note:<version>',
  status     VARCHAR(16) NOT NULL DEFAULT 'completed' COMMENT 'completed/skipped/read',
  updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, state_key),
  KEY idx_user_updated (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='新手引导完成/跳过与公告已读状态(按用户)';

-- ----------------------------------------------------------------------------
-- 3) 菜单与权限登记
--    版本公告管理页 = 管理员内容发布入口, 归系统配置「系统」分组末项(sort=75,
--    与 SystemLayout 的 SYSTEM_MENU_ITEMS 顺序一致)。
-- ----------------------------------------------------------------------------
INSERT INTO adh_menu_registry (menu_key, label, section, module, sort, perm_code, ai_access) VALUES
  ('system:release-notes', '版本公告', '系统', 'system', 75, 'release-note:manage', 'none')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), section = VALUES(section), module = VALUES(module),
  sort = VALUES(sort), perm_code = VALUES(perm_code), ai_access = VALUES(ai_access), is_active = 1;

INSERT IGNORE INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, ai_access, ai_action_key, ai_note) VALUES
  ('release-note:manage', '版本公告发布', 'system', '系统配置',
   '新增/编辑/删除版本更新公告内容', '/api/admin/release-notes*', 'POST,PUT,PATCH,DELETE',
   'system:release-notes', 750, 'none', '',
   '公告内容与引导状态属管理员内容发布与个人 UI 状态, 不作为 AI 工具面开放');

-- 菜单权限码回填(与既有迁移同口径: 菜单行缺失 perm_code 时从权限点反查)
UPDATE adh_menu_registry mr
  JOIN adh_perm_registry p ON p.menu_key = mr.menu_key
  SET mr.perm_code = p.perm_code
  WHERE p.menu_key <> '' AND (mr.perm_code IS NULL OR mr.perm_code = '');

-- 存量角色默认授权: **本迁移刻意不给普通角色授 release-note:manage**。
-- 理由(menu-permission-sync §2.3 的"维持改前可见现状"在这里等于不授权):
--   * 这是管理员内容发布页, 改前系统里没有该入口, 默认授权反而会开出无人可配的入口;
--   * SystemLayout 对非 admin 硬跳转, 普通角色即使勾到菜单也进不去, 属空承诺;
--   * 公告的**读取**不需要权限码: /api/release-notes 走个人自助路由(_SELF_ROUTE_PATTERNS),
--     与 /api/memory 同口径, 全体登录用户可见已发布公告并可写自己的引导状态。
-- 管理员侧无需授权: check_api_permission 与 permissionStore 对 role='admin' 一律放行。

-- ----------------------------------------------------------------------------
-- 4) 种子公告: 本次能力(升级触发链路开箱可验证)
--    INSERT IGNORE(version 唯一): 管理员改过这条公告后再跑迁移不得覆盖回去。
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO adh_release_notes
  (version, title, summary, sections, guide_key, is_major, is_published, published_at, updated_by) VALUES
  ('1.1.0', '新手引导与版本更新公告',
   '首次登录或版本升级后有分步引导，顶栏新增版本更新公告入口',
  JSON_ARRAY(
    JSON_OBJECT('kind', 'feature', 'items', JSON_ARRAY(
      '新手引导：首次登录自动弹出欢迎总览，可按模块分步高亮讲解页面功能项',
      '版本更新公告：右上角模块切换左侧新增公告按钮，未读版本带红点，点开查看版本功能与更新项',
      '引导可随时跳过（本步/本条/全部），也可在用户菜单「新手引导」里重新观看'
    )),
    JSON_OBJECT('kind', 'improvement', 'items', JSON_ARRAY(
      '公告内容由管理员在「系统配置 → 版本公告」维护，无需重新发版即可发布更新说明'
    ))
  ),
  'welcome', 1, 1, UTC_TIMESTAMP(), 'system');

-- ============================================================================
-- 内容中心（一级模块 /content）— 建表 + 菜单/权限登记（幂等，可重复执行）
--
-- 背景:
--   * 技术白皮书 / 临床应用 / 合规解读这类"给人看的内容"此前没有编排处。
--     新建一级模块「内容中心」只负责 **创作 → 审核 → 发布** 这段生产闭环。
--   * **资讯门户不在本模块**（用户 2026-11-04 拍板）: 门户是给全员消费的独立模块,
--     尚未开发。因此本批不登记 content:feed 菜单、不登记 article:read 权限码 ——
--     提前登记会造出"角色页能勾、侧栏没入口"的死开关（menu-permission-sync §6）。
--     门户那一批再一并补: 菜单 + article:read + 存量角色默认授权（维持"全员可读"现状）。
--   * 内容与取数护城河无关: 内容不落数据源、不返回数据行, 因此不经 semantics.execute /
--     permission_enforcer, 也不新增任何"返回数据行而不经治理"的通道。
--   * 边界（防模块归属漂移, 三条判定准则）:
--       1) 快速路径 / SQL 示例对**不在本模块** —— 改了它会改变 AI 取数行为, 属数据中台语义层;
--       2) 版本公告**不在本模块** —— 它是平台自身运维通告, 留系统配置;
--       3) 「在哪开同步」属知识库页(管道属性), 「什么进了库/是否过期/撤回是否失败」属语料同步页(内容状态)。
--       4) **知识库文档(语料正文)在内容中心, 知识库管理留在系统配置**（本文件 §14）:
--          新菜单 content:kb-docs 只查看/编辑 notebook 内部的**目录树文档**（kbdoc:read/write,
--          pattern /api/content/knowledge-bases*）; 建库、绑 AS-BOT、配凭据仍是
--          system:knowledge-base + kb:read/kb:manage（pattern /api/knowledge-bases*）, 一行不动。
--
-- 口径(实现与本文件必须一致):
--   * 命名: menu_key = `content:<页>`(模块前缀), 权限码 = `article:<动作>`(对象前缀)。
--     两者不得同字, 否则前端 allowed('content:review') 分不清是页面入口还是动作权限。
--   * 门控唯一在 API 权限中间件(backend/common/api_permission.py, 按 api_pattern × api_method
--     交叉匹配)。因此**发布与审核走独立路径前缀**: /api/content/publish*、/api/content/reviews*,
--     不与 /api/content/articles* 共用一个权限码的 pattern 集 —— 交叉匹配做不了减法,
--     若 article:manage 持有 POST /api/content/articles*, 就会顺带把 publish 也授出去。
--     handler 内不另造第二套"谁能做什么"的判断。
--   * 本模块内的读（清单/详情/词表）挂在 article:manage 的 GET 上: 只勾"编辑内容条目"
--     的角色必须能加载它要管理的那份清单, 否则管理页一进去就 403。面向读者的
--     article:read 属门户模块, 不在本批。不加 _SELF_ROUTE_PATTERNS 白名单:
--     保持权限裁决点唯一, 管理员可按角色收回入口。
--   * 审核与发布 ai_access='none'(人专属): 对外内容代表公司立场, 必须有人负责;
--     与「评测用例不得自评自证」同构。AI 侧只开放 写草稿 / 提交审核（读与同步随后续批次）。
--
-- 执行: mysql -h <host> -P <port> -u root -p < content_center_migration.sql
-- ============================================================================
USE adh2;

-- ----------------------------------------------------------------------------
-- 1) 栏目（可编排: 新增/改名/排序/停用, 这是"编排通用内容"的落点）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_content_categories (
  id          BIGINT      NOT NULL AUTO_INCREMENT,
  category_key VARCHAR(64) NOT NULL COMMENT '栏目标识(内部, 不外泄为选择项)',
  label       VARCHAR(128) NOT NULL COMMENT '栏目名(UI 与 AI 工具面均以此显示)',
  description VARCHAR(256) NOT NULL DEFAULT '' COMMENT '栏目定位(侧栏副标题)',
  icon        VARCHAR(16)  NOT NULL DEFAULT '' COMMENT '图标(emoji 或图标名)',
  color       VARCHAR(16)  NOT NULL DEFAULT '' COMMENT '主色(缺省封面占位用)',
  sort        INT          NOT NULL DEFAULT 0,
  is_active   TINYINT(1)   NOT NULL DEFAULT 1,
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_category_key (category_key),
  KEY idx_active_sort (is_active, sort)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容栏目(口腔数字化业务口径, 管理员可编排)';

-- ----------------------------------------------------------------------------
-- 2) 内容条目
--    正文**不在本表**: 正文存 revision, 由 head/live 两个指针指向。
--    这样"编辑已发布内容"只产生新 revision, 线上继续展示 live, 不会出现
--    草稿改一半就全员可见; 回滚 = live 指回历史 revision, 不必重写正文。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_content_articles (
  id               BIGINT       NOT NULL AUTO_INCREMENT,
  slug             VARCHAR(128) NOT NULL COMMENT '对外标识(URL 用, 不用裸 id)',
  title            VARCHAR(200) NOT NULL COMMENT '标题(唯一事实源, 正文不再写 H1)',
  summary          VARCHAR(500) NOT NULL DEFAULT '' COMMENT '摘要(卡片与列表用)',
  cover_object_key VARCHAR(256) NOT NULL DEFAULT '' COMMENT '封面对象 key(空=栏目主色占位; 不外泄给前端)',
  category_id      BIGINT       NOT NULL DEFAULT 0,
  tags             JSON         NULL COMMENT '标签数组(必须取自 adh_content_tag_vocab)',
  kind             VARCHAR(16)  NOT NULL DEFAULT 'markdown' COMMENT 'markdown|link|file|promoted',
  link_url         VARCHAR(512) NOT NULL DEFAULT '' COMMENT '外链条目目标(kind=link)',
  link_label       VARCHAR(128) NOT NULL DEFAULT '' COMMENT '外链来源业务可读名',
  pinned           TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '1=置顶',
  featured         TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '1=精选',
  source_type      VARCHAR(16)  NOT NULL DEFAULT 'manual' COMMENT 'manual|report|conversation',
  source_ref       VARCHAR(128) NOT NULL DEFAULT '' COMMENT '提升来源内部溯源(不进响应体)',
  refs             JSON         NULL COMMENT '外部出处清单 [{title,url}]。无出处的数字不得写进正文; 缺失是软警告',
  head_revision_id BIGINT       NOT NULL DEFAULT 0 COMMENT '当前编辑中的版本',
  live_revision_id BIGINT       NOT NULL DEFAULT 0 COMMENT '线上展示版本(0=未发布)',
  status           VARCHAR(16)  NOT NULL DEFAULT 'draft' COMMENT 'draft|in_review|approved|published|scheduled|archived',
  publish_at       DATETIME     NULL COMMENT '定时生效时间(未来=scheduled, P2)',
  published_at     DATETIME     NULL COMMENT '首次发布时间(列表按此倒序)',
  archived_at      DATETIME     NULL,
  review_by        DATE         NULL COMMENT '复审到期日(含外部数字/法规条款的条目必填)',
  version          INT          NOT NULL DEFAULT 1 COMMENT '乐观锁(写操作带 expected_version)',
  view_count       INT          NOT NULL DEFAULT 0,
  created_by       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '作者用户名(显示用 name, 不显示 id)',
  updated_by       VARCHAR(64)  NOT NULL DEFAULT '',
  created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_article_slug (slug),
  KEY idx_status_published (status, published_at),
  KEY idx_category_status (category_id, status),
  KEY idx_live_revision (live_revision_id),
  KEY idx_review_by (review_by)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容中心条目(全站公开静态内容, 不受数据权限过滤)';

-- ----------------------------------------------------------------------------
-- 3) 版本快照（一次保存产生新行; 已发布过的 revision 不得原地修改）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_content_revisions (
  id           BIGINT      NOT NULL AUTO_INCREMENT,
  article_id   BIGINT      NOT NULL,
  body         MEDIUMTEXT  NULL COMMENT '正文(markdown)或解读文字; chart 块只存已渲染快照数据',
  format       VARCHAR(16) NOT NULL DEFAULT 'markdown' COMMENT 'markdown|plain',
  change_note  VARCHAR(256) NOT NULL DEFAULT '' COMMENT '本次改动说明',
  content_hash CHAR(32)    NOT NULL DEFAULT '' COMMENT '正文 MD5(幂等导入与去重用)',
  created_by   VARCHAR(64) NOT NULL DEFAULT '',
  created_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_article_created (article_id, created_at),
  KEY idx_hash (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容版本快照(审核的是哪一版靠它追溯)';

-- ----------------------------------------------------------------------------
-- 4) 审核流水（回答"这个版本谁批的、当时警告了什么"）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_content_reviews (
  id             BIGINT      NOT NULL AUTO_INCREMENT,
  article_id     BIGINT      NOT NULL,
  revision_id    BIGINT      NOT NULL DEFAULT 0 COMMENT '审核针对的版本快照',
  action         VARCHAR(16) NOT NULL COMMENT 'submit|approve|reject|waive|reassign|publish|archive|republish',
  comment        VARCHAR(512) NOT NULL DEFAULT '' COMMENT '驳回必填; 免审直发必填原因',
  gate_warnings  JSON        NULL COMMENT '软警告逐条确认结果 [{code,message,acknowledged}]',
  actor          VARCHAR(64) NOT NULL DEFAULT '' COMMENT '操作人用户名(服务端注入, 不信任请求体)',
  actor_channel  VARCHAR(16) NOT NULL DEFAULT 'human' COMMENT 'human|ai(区分人写与 AI 写)',
  created_at     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_article_created (article_id, created_at),
  KEY idx_revision (revision_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容审核与发布流水(留痕, 含软警告确认)';

-- ----------------------------------------------------------------------------
-- 5) 知识库同步水位线（仿 adh_ontology_kb_sync_state: 成败必须落表, 只写 warning 视为缺陷）
--    P1 启用; 表先建好, 避免 P1 再改结构。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_content_kb_sync (
  article_id  BIGINT      NOT NULL,
  kb_id       BIGINT      NOT NULL COMMENT '目标知识库(adh_knowledge_bases.id, 不外泄给前端)',
  revision_id BIGINT      NOT NULL DEFAULT 0,
  state       VARCHAR(24) NOT NULL DEFAULT 'pending' COMMENT 'pending|synced|failed|withdrawn|withdraw_failed',
  error       VARCHAR(512) NOT NULL DEFAULT '' COMMENT '失败原因(含 degraded 事实)',
  synced_at   DATETIME    NULL,
  updated_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (article_id, kb_id, revision_id),
  KEY idx_state (state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容→知识库语料同步水位线';

-- ----------------------------------------------------------------------------
-- 6) 受控标签词表（标签只能从词表选, 禁止自由输入）
--    首批成稿踩过这个坑: 26 个标签里 21 个只命中 1 篇, 侧栏「热门标签」全是孤儿标签,
--    点进去只有一个结果, 筛选形同虚设。词表受控 + 标签云只渲染命中 ≥2 篇。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_content_tag_vocab (
  id        BIGINT      NOT NULL AUTO_INCREMENT,
  tag       VARCHAR(64) NOT NULL,
  sort      INT         NOT NULL DEFAULT 0,
  is_active TINYINT(1)  NOT NULL DEFAULT 1,
  created_at DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_tag (tag),
  KEY idx_active_sort (is_active, sort)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容标签受控词表';

-- ----------------------------------------------------------------------------
-- 7) 附件（P1 已启用上传通道：对象存储 key 由 build_content_file_key 单点生成）
--    两条口径写在这里，因为它们决定了表结构“为什么没有那一列”:
--    · **不进 revision 版本链**（无 revision_id）：正文有版本与回滚，附件只有当前
--      挂着那几个; 因此换文件不会把已发布内容打回草稿, 门户下载立即变（界面已声明）。
--    · **只下载不站内预览**：服务端下载头恒为 attachment，所以不需存 view_mode；
--      inline 渲染上传件 = 同源存储型 XSS，office 版式也还原不了。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_content_files (
  id         BIGINT      NOT NULL AUTO_INCREMENT,
  article_id BIGINT      NOT NULL,
  object_key VARCHAR(256) NOT NULL COMMENT '对象存储 key(内部字段, 不外泄给前端/LLM)',
  filename   VARCHAR(256) NOT NULL COMMENT '显示与下载用文件名',
  mime       VARCHAR(128) NOT NULL DEFAULT '',
  size       BIGINT       NOT NULL DEFAULT 0,
  sort       INT          NOT NULL DEFAULT 0,
  created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_article_sort (article_id, sort)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容附件';

-- ----------------------------------------------------------------------------
-- 8) 个人已读/收藏（与 adh_user_guide_state 同构但按文章索引, 便于"我的收藏"列表）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_content_user_state (
  user_id    BIGINT      NOT NULL COMMENT '属主(身份只取服务端 JWT)',
  article_id BIGINT      NOT NULL,
  status     VARCHAR(16) NOT NULL COMMENT 'read|bookmarked',
  updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (user_id, article_id, status),
  KEY idx_user_status (user_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='内容个人已读/收藏状态';

-- ----------------------------------------------------------------------------
-- 9) 栏目种子（口腔数字化业务口径; INSERT IGNORE 保证管理员改过后再跑不被覆盖回去）
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO adh_content_categories (category_key, label, description, icon, color, sort, is_active) VALUES
  ('industry',    '齿科行业趋势', '口腔数字化市场、椅旁修复、装机与政策趋势', '📈', '#6366f1', 1, 1),
  ('whitepaper',  '技术白皮书',   '扫描精度、AI 建模、材料与设计余量、数据互操作', '🔬', '#0891b2', 2, 1),
  ('clinic',      '临床应用',     '种植/正畸/修复/全口与技师所工作流', '🦷', '#16a34a', 3, 1),
  ('product',     '产品动态',     '固件、软件、开放接口与配件更新', '✨', '#db2777', 4, 1),
  ('compliance',  '合规与标准',   '器械注册、ISO 13485 体系、影像数据合规', '🛡️', '#b45309', 5, 1),
  ('insight',     '数据洞察',     '脱敏聚合的行业与装机数据复盘', '📊', '#ea580c', 6, 1);

-- 标签词表种子（首批 14 个受控词, 与 docs/content/insights/tags.json 同源）
INSERT IGNORE INTO adh_content_tag_vocab (tag, sort, is_active) VALUES
  ('扫描精度', 1, 1), ('质量控制', 2, 1), ('临床操作', 3, 1), ('隔湿与止血', 4, 1),
  ('种植与导板', 5, 1), ('全口修复', 6, 1), ('设备管理', 7, 1), ('失败归因', 8, 1),
  ('固件与发布', 9, 1), ('质量体系', 10, 1), ('法规与标准', 11, 1), ('数据安全', 12, 1),
  ('市场与渗透率', 13, 1), ('数据口径', 14, 1);

-- ----------------------------------------------------------------------------
-- 10) 菜单登记（module='content', 4 项; section 与前端 ContentLayout 侧栏分组同名,
--     sort 与侧栏顺序一致 —— menu-permission-sync §2.1）
-- ----------------------------------------------------------------------------
INSERT INTO adh_menu_registry (menu_key, label, section, module, sort, perm_code, ai_access, is_active) VALUES
  ('content:drafts',   '我的草稿', '我的工作台', 'content', 10, 'article:manage', 'write', 1),
  ('content:manage',   '内容管理', '内容生产',   'content', 20, 'article:manage', 'write', 1),
  ('content:review',   '审核发布', '内容生产',   'content', 30, 'article:review', 'none',  1),
  ('content:taxonomy', '栏目与标签','编排配置',   'content', 40, 'article:manage', 'write', 1)
ON DUPLICATE KEY UPDATE
  label = VALUES(label), section = VALUES(section), module = VALUES(module),
  sort = VALUES(sort), perm_code = VALUES(perm_code), ai_access = VALUES(ai_access), is_active = 1;

-- ----------------------------------------------------------------------------
-- 11) 权限点登记（三个码职责分开: 写草稿 / 审核 / 发布; 读属门户模块的 article:read 不在本批）
--     「语料同步」(content:sync + article:sync) 属后续批次, 与它的页面/端点同批登记;
--     「资讯门户」(content:feed + article:read) 属独立模块, 同样不提前登记。
--     api_pattern 与 backend/modules/platform/api/content.py 的挂载路径逐条对应。
-- ----------------------------------------------------------------------------
INSERT INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note) VALUES
  ('article:manage', '编辑内容条目', 'content', '内容中心',
   '新建/修改/删除内容草稿、编排栏目与标签、提交审核。只写 head revision, 不触线上',
   '/api/content/articles*,/api/content/categories*,/api/content/tags*', 'GET,POST,PUT,PATCH,DELETE',
   'content:manage', 811, 1, 'write', '',
   'AI 可写草稿与提交审核（改动只落草案）; 发布/下架/审核不在此码内, 由下面两码独立把关且 ai_access=none'),
  ('article:review', '审核内容', 'content', '内容中心',
   '查看待审队列, 通过或驳回内容（驳回必填原因; 作者不得自审自己的稿）',
   '/api/content/reviews*', 'GET,POST',
   'content:review', 812, 1, 'none', '',
   '审核是人的判断, 不得由 AI 自审自批（与「评测用例不得自评自证」同构）。放开需显式改配置'),
  ('article:publish', '发布与下架内容', 'content', '内容中心',
   '把审核通过的版本置为线上可见（或免审直发）, 以及下架。发布对全体登录用户可见, 不受数据权限过滤',
   '/api/content/publish*', 'POST',
   'content:review', 813, 1, 'none', '',
   '对外内容代表公司立场, 必须有人负责: 发布/下架不开放 AI 调用。免审直发需本码 + 必填原因入审计')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), module = VALUES(module), module_label = VALUES(module_label),
  description = VALUES(description), api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
  menu_key = VALUES(menu_key), sort = VALUES(sort), ai_access = VALUES(ai_access),
  ai_action_key = VALUES(ai_action_key), ai_note = VALUES(ai_note), is_active = 1;

-- 菜单 perm_code 以权限点为真值源回填（幂等重跑时防两侧飘移）
UPDATE adh_menu_registry m
  JOIN adh_perm_registry p ON p.menu_key = m.menu_key AND p.module = 'content'
SET m.perm_code = p.perm_code
WHERE m.module = 'content' AND FIND_IN_SET('GET', p.api_method);

-- ----------------------------------------------------------------------------
-- 11.5) 收敛上一版残留: 「语料同步」与「资讯门户」都不属本批
--       （无端点/无页面/无入口的登记行就是角色页里的死开关, 必须删干净）
-- ----------------------------------------------------------------------------
DELETE FROM adh_role_perms WHERE perm_code IN ('article:sync', 'article:read');
DELETE FROM adh_menu_registry WHERE menu_key IN ('content:sync', 'content:feed');
DELETE FROM adh_perm_registry WHERE perm_code IN ('article:sync', 'article:read');

-- ----------------------------------------------------------------------------
-- 12) 存量角色默认授权: 本批**一个都不授**
--     内容中心是全新能力（创作/审核/发布), 默认授权等于给所有角色扩写权限;
--     admin 由中间件天然放行, 其余角色由管理员在「角色与菜单」里显式配置。
--     "全员可读"的默认授权属门户批次（article:read + content:feed 同批补）。
-- ----------------------------------------------------------------------------
SELECT 1;

-- ----------------------------------------------------------------------------
-- 13) 核验（应用后人工确认, 勿删）
-- ----------------------------------------------------------------------------
-- SELECT menu_key,label,section,module,sort,perm_code,ai_access FROM adh_menu_registry
--   WHERE module='content' ORDER BY sort;
-- SELECT perm_code,label,menu_key,api_pattern,api_method,ai_access,ai_note FROM adh_perm_registry
--   WHERE perm_code LIKE 'article:%' ORDER BY sort;
-- SELECT r.name, rp.perm_code FROM adh_roles r JOIN adh_role_perms rp ON rp.role_id=r.id
--   WHERE rp.perm_code LIKE 'article:%' ORDER BY r.name, rp.perm_code;
-- 模拟某角色菜单反查（只有 article:review 的角色应只见 content:review 一项）:
-- SELECT DISTINCT m.menu_key FROM adh_menu_registry m
--   JOIN adh_role_perms rp ON rp.perm_code = m.perm_code
--   JOIN adh_roles r ON r.id = rp.role_id
--   WHERE r.name='analyst' AND m.menu_key LIKE 'content:%' ORDER BY m.sort;
-- 表结构确认: SHOW TABLES LIKE 'adh_content_%';

-- ----------------------------------------------------------------------------
-- 14) 知识库文档：内容中心新菜单 + 两个权限点（本批随 KB 目录树页同批登记）
--     边界（用户 2026-11-04 拍板）: 知识库**管理**留在系统配置
--     （system:knowledge-base + kb:read/kb:manage, pattern /api/knowledge-bases*）,
--     这里只登记**库内部文档**的查看与编辑（pattern /api/content/knowledge-bases*）。
--     为什么不复用 kb:read / kb:manage: 那两个授出的是"看/改库配置(类型与连接参数)",
--     与"改云端语料正文"必须能分开勾; 复用 article:manage 也不对 —— 门户内容有
--     创作→审核→发布链条, 语料没有, 是两拨人两回事。
--     ai_access: 读='read'（AS-BOT 本来就在检索这些文档）, 写='none' —— 让 AI 自己
--     改自己的检索语料是自我强化回路（与「评测用例不得自评自证」同构）, 放开需显式改配置。
-- ----------------------------------------------------------------------------
INSERT INTO adh_menu_registry (menu_key, label, section, module, sort, perm_code, ai_access, is_active) VALUES
  ('content:kb-docs', '知识库文档', '知识运营', 'content', 50, 'kbdoc:read', 'read', 1)
ON DUPLICATE KEY UPDATE
  label = VALUES(label), section = VALUES(section), module = VALUES(module),
  sort = VALUES(sort), perm_code = VALUES(perm_code), ai_access = VALUES(ai_access), is_active = 1;

INSERT INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note) VALUES
  ('kbdoc:read', '查看知识库文档', 'content', '内容中心',
   '按目录树浏览云端知识库（qmind notebook）里的文档与正文。不含知识库配置（建库/绑 AS-BOT/凭据在系统配置）',
   '/api/content/knowledge-bases*', 'GET',
   'content:kb-docs', 814, 1, 'read', '',
   'AI 可读（检索即读）；写不在此码内'),
  ('kbdoc:write', '编辑知识库文档', 'content', '内容中心',
   '保存/新建云端知识库里的文档与目录。只提交改动的那一篇；本体上云的派生文档（带 model-version-stamp）一律拒改',
   '/api/content/knowledge-bases*', 'POST,PUT',
   'content:kb-docs', 815, 1, 'none', '',
   '语料是 AS-BOT 的回答依据, 改它等于改 AI 的知识: 只允许人做, 不开放 AI 调用。每次写入落审计')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), module = VALUES(module), module_label = VALUES(module_label),
  description = VALUES(description), api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
  menu_key = VALUES(menu_key), sort = VALUES(sort), ai_access = VALUES(ai_access),
  ai_action_key = VALUES(ai_action_key), ai_note = VALUES(ai_note), is_active = 1;

-- 菜单 perm_code 以权限点为真值源回填（与 §11 同一条语句, 覆盖到本批新码）
UPDATE adh_menu_registry m
  JOIN adh_perm_registry p ON p.menu_key = m.menu_key AND p.module = 'content'
SET m.perm_code = p.perm_code
WHERE m.module = 'content' AND FIND_IN_SET('GET', p.api_method);

-- ----------------------------------------------------------------------------
-- 14.5) 存量角色默认授权: 仍**一个都不授**（与 §12 同一决策）
--       内容中心整体是全新能力, 文档编辑权等同把"改 AI 知识"的口子默认发给所有角色;
--       admin 由中间件天然放行, 其余角色由管理员在「角色与权限」里显式勾选。
-- ----------------------------------------------------------------------------
SELECT 1;

-- 核验（应用后人工确认）:
-- SELECT menu_key,label,section,module,sort,perm_code,ai_access FROM adh_menu_registry
--   WHERE module='content' ORDER BY sort;
-- SELECT perm_code,label,menu_key,api_pattern,api_method,ai_access FROM adh_perm_registry
--   WHERE perm_code LIKE 'kbdoc:%' OR perm_code LIKE 'article:%' ORDER BY sort;
-- 只勾 kbdoc:read 的角色应只见 GET /api/content/knowledge-bases*, 拿不到 PUT/POST:
-- SELECT r.name, p.perm_code, p.api_method FROM adh_role_perms rp
--   JOIN adh_roles r ON r.id=rp.role_id JOIN adh_perm_registry p ON p.perm_code=rp.perm_code
--   WHERE p.perm_code LIKE 'kbdoc:%' ORDER BY r.name, p.perm_code;

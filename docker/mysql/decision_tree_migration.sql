-- ============================================================================
-- 决策建议（Decision Advice）能力落地 · 第 0 批：资产表 + 权限/菜单登记
--
-- 状态：实施中（P0）　日期：2026-11-12　口径来源：计划「决策建议能力落地方案 v2」
-- 范围边界：只建三张资产表 + 登记权限码/菜单 + 对齐数据中台菜单段与顺序。
--          不种子任何决策树（默认全关 + 未经人工评审的判断标准不入生产库）；
--          不动任何角色的 is_active/授权现状（见 §5）。
--
-- 为什么要这个能力面：平台已有「数据分析（发生了什么）」与「知识沉淀（为什么/
-- 标准怎么做）」两层，缺「该做什么」这一层。本批把决策树做成**一等公民资产**，
-- 条件求值由确定性引擎完成（LLM 不参与判断本身），四个出口同源消费同一引擎。
--
-- 三个设计纪律（与既有规范一一对应，改动前必读）：
--   1. 单一可编辑事实源 = `tree_doc` JSON（对齐 ontology-modeling §1）。
--      `required_metrics` / `bound_object_keys` 是**发布时重算的派生列**，
--      任何代码/迁移不得独立编辑它们（派生视图独立编辑 = 双事实源）。
--   2. 草案/发布两段式（对齐 as-bot-system-waker §2）：`status='draft'` 不参与
--      评估，只有 `active` 生效；发布是独立动作 + 独立权限码 `decision:publish`。
--   3. 站点适用性只登记业务名（`datasource` 列存业务名，不存 datasource_id），
--      可查站点仍是 role 授权的派生视图，不新建第二套裁决（waker-datasource §1）。
--
-- 幂等：全部 CREATE TABLE IF NOT EXISTS / INSERT ... ON DUPLICATE KEY UPDATE /
--      定向 UPDATE；可重复执行。执行方式：
--      venv/bin/python logs/apply_migration.py docker/mysql/decision_tree_migration.sql
-- ============================================================================

USE adh2;

-- ----------------------------------------------------------------------------
-- 1) 决策树资产表（唯一可编辑事实源 = tree_doc）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_decision_trees (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  tree_key         VARCHAR(128) NOT NULL COMMENT '稳定标识（对外引用用它，不用自增 id）',
  name             VARCHAR(200) NOT NULL COMMENT '业务可读名',
  description      VARCHAR(512) NOT NULL DEFAULT '' COMMENT '这棵树回答什么判断',
  workspace_id     BIGINT NOT NULL DEFAULT 0 COMMENT '归属空间（0=全局口径资产，跨空间共享）',
  status           ENUM('draft','active','retired') NOT NULL DEFAULT 'draft'
                   COMMENT '草案/发布两段式：只有 active 参与评估',
  tree_doc         JSON NULL COMMENT '**唯一可编辑事实源**：条件树（branch/leaf + when + advice_key）',
  required_metrics JSON NULL COMMENT '派生：doc 内全部指标/维度业务名（发布时重算，禁手改）',
  bound_object_keys JSON NULL COMMENT '派生：命中的本体对象 key（发布门禁查悬空，对齐 ontology §4）',
  apply_sites      JSON NULL COMMENT '站点适用性 [{site_code, datasource(业务名)}]；空=沿用角色授权站点派生',
  schedule_cron    VARCHAR(64) NOT NULL DEFAULT '' COMMENT '主动预警周期（空=不调度）',
  timezone         VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai' COMMENT 'cron 墙钟时区',
  notify_channel_id BIGINT NOT NULL DEFAULT 0 COMMENT '通知渠道引用（不复制渠道配置）',
  version          INT NOT NULL DEFAULT 1 COMMENT '发布版本号（每次发布 +1，实例据此归因）',
  created_by       BIGINT NOT NULL DEFAULT 0 COMMENT '服务端注入，不信任请求体（护栏 §2）',
  updated_by       BIGINT NOT NULL DEFAULT 0,
  published_by     BIGINT NOT NULL DEFAULT 0,
  published_at     DATETIME NULL,
  is_active        TINYINT(1) NOT NULL DEFAULT 1 COMMENT '软删开关（与 status 正交：retired 仍可见历史）',
  created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_decision_tree_key (tree_key),
  INDEX idx_decision_tree_status (status, is_active),
  INDEX idx_decision_tree_ws (workspace_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='决策树（业务判断口径资产，tree_doc 为唯一事实源）';

-- ----------------------------------------------------------------------------
-- 2) 建议动作文案库
--    叶子只存 advice_key 引用，同一建议文案不在多棵树复制（对齐 ontology §5
--    「同一概念只登记一处」）；渲染时库优先、快照只兜底（ui-vis-library-style §3）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_decision_advice (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  advice_key   VARCHAR(128) NOT NULL COMMENT '稳定标识，决策树叶子按此引用',
  action       VARCHAR(512) NOT NULL COMMENT '建议动作（业务语言，不写物理名）',
  rationale    TEXT NULL COMMENT '为什么这么建议（口径说明）',
  severity     ENUM('low','medium','high','critical') NOT NULL DEFAULT 'medium'
               COMMENT '沿用质量规则四档（adh_quality_rules.severity 同口径）',
  kb_refs      JSON NULL COMMENT '企业知识出处：内容中心已发布文章 slug 清单',
  workspace_id BIGINT NOT NULL DEFAULT 0,
  is_active    TINYINT(1) NOT NULL DEFAULT 1 COMMENT '下架不删行：历史实例仍靠快照渲染',
  created_by   BIGINT NOT NULL DEFAULT 0,
  updated_by   BIGINT NOT NULL DEFAULT 0,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_decision_advice_key (advice_key),
  INDEX idx_decision_advice_ws (workspace_id, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='决策建议动作文案库';

-- ----------------------------------------------------------------------------
-- 3) 评估实例（纯展示：无处置状态列 —— 用户拍板「建议只读，不接处置闭环」）
--    evidence 只存业务名/比较符/阈值/实测值；物理表列名、datasource_id、SQL
--    一律不落本表（护栏 §7：本表是对外可读面，不是调试面）。
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS adh_decision_runs (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  tree_id       BIGINT NOT NULL,
  tree_key      VARCHAR(128) NOT NULL COMMENT '冗余存 key：树被删后实例仍可说清是谁给的判断',
  tree_version  INT NOT NULL COMMENT '按哪个发布版本求值（可复现）',
  trigger_type  ENUM('chat_turn','manual','scheduled','dashboard') NOT NULL
                COMMENT '评估由哪个出口发起',
  user_id       BIGINT NOT NULL DEFAULT 0 COMMENT '服务端注入的可信身份（护栏 §2）',
  workspace_id  BIGINT NOT NULL DEFAULT 0,
  site_code     VARCHAR(64) NOT NULL DEFAULT '' COMMENT '本次生效站点（业务代码，非数据源标识）',
  conversation_id BIGINT NOT NULL DEFAULT 0 COMMENT 'Chat 出口回填：回放按此定位实例，不重算',
  message_uuid  VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'Chat 回合定位键（消息由前端持久化，服务端在 done 时拿不到 seq）',
  message_ordinal   INT NOT NULL DEFAULT -1,
  run_key       VARCHAR(160) NOT NULL DEFAULT ''
                COMMENT '定时出口的墙钟分钟幂等键（Beat 重投不双发预警）；其余出口为空',
  evidence      JSON NULL COMMENT '脱敏后依据：指标业务名/比较符/阈值/实测值',
  hit_path      JSON NULL COMMENT '命中路径（节点 label 序列）',
  advice_snapshot JSON NULL COMMENT '建议快照（库优先，此处只兜底已下架情形）',
  verdict       ENUM('hit','no_hit','partial','skipped') NOT NULL
                COMMENT 'partial=本轮取数不足以完整求值；skipped=按原因未评估',
  verdict_reason VARCHAR(512) NOT NULL DEFAULT ''
                COMMENT '非 hit 必须带可诊断原因（no-silent-degradation §1）',
  elapsed_ms    INT NOT NULL DEFAULT 0,
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_decision_runs_tree (tree_id, created_at),
  INDEX idx_decision_runs_conv (conversation_id, message_ordinal),
  INDEX idx_decision_runs_msg (conversation_id, message_uuid),
  INDEX idx_decision_runs_user_ws (user_id, workspace_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='决策树评估实例（只读展示，无处置状态）';

-- P1 接线补列：本批先前已在存量库建过表，CREATE TABLE IF NOT EXISTS 不会补列，
-- 所以走 information_schema 守卫的幂等 ALTER（反复执行不报错也不重复加列）。
SET @add_msg_uuid := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = 'adh2' AND TABLE_NAME = 'adh_decision_runs'
           AND COLUMN_NAME = 'message_uuid'),
  'SELECT 1',
  'ALTER TABLE adh_decision_runs ADD COLUMN message_uuid VARCHAR(64) NOT NULL DEFAULT '''' COMMENT ''Chat 回合定位键'' AFTER conversation_id');
PREPARE stmt FROM @add_msg_uuid; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_msg_idx := IF(
  EXISTS(SELECT 1 FROM information_schema.STATISTICS
         WHERE TABLE_SCHEMA = 'adh2' AND TABLE_NAME = 'adh_decision_runs'
           AND INDEX_NAME = 'idx_decision_runs_msg'),
  'SELECT 1',
  'ALTER TABLE adh_decision_runs ADD INDEX idx_decision_runs_msg (conversation_id, message_uuid)');
PREPARE stmt FROM @add_msg_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_run_key := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = 'adh2' AND TABLE_NAME = 'adh_decision_runs'
           AND COLUMN_NAME = 'run_key'),
  'SELECT 1',
  'ALTER TABLE adh_decision_runs ADD COLUMN run_key VARCHAR(160) NOT NULL DEFAULT '''' COMMENT ''定时出口幂等键'' AFTER message_ordinal');
PREPARE stmt FROM @add_run_key; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 标识列宽度修正（2026-11-12 端到端核验发现）：本批先前把 id / 引用列写成 INT，
-- 而全库的 id 口径是 BIGINT（adh_users.id / adh_workspaces.id /
-- adh_notification_channels.id 都是时间戳形态的 13~16 位数字，实测超 INT 上限
-- → 保存草案与文案直接 500：Out of range value for column 'created_by'）。
-- CREATE TABLE IF NOT EXISTS 不会改已在库的列，所以同批补一段幂等 MODIFY：
-- 已是 bigint 就 SELECT 1，可反复执行。（version / tree_version / elapsed_ms /
-- message_ordinal 不是标识列，沿用 INT。）
SET @widen_trees := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = 'adh2' AND TABLE_NAME = 'adh_decision_trees'
           AND COLUMN_NAME IN ('id','workspace_id','notify_channel_id','created_by','updated_by','published_by')
           AND DATA_TYPE <> 'bigint'),
  'ALTER TABLE adh_decision_trees MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN workspace_id BIGINT NOT NULL DEFAULT 0, MODIFY COLUMN notify_channel_id BIGINT NOT NULL DEFAULT 0, MODIFY COLUMN created_by BIGINT NOT NULL DEFAULT 0, MODIFY COLUMN updated_by BIGINT NOT NULL DEFAULT 0, MODIFY COLUMN published_by BIGINT NOT NULL DEFAULT 0',
  'SELECT 1');
PREPARE stmt FROM @widen_trees; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @widen_advice := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = 'adh2' AND TABLE_NAME = 'adh_decision_advice'
           AND COLUMN_NAME IN ('id','workspace_id','created_by','updated_by')
           AND DATA_TYPE <> 'bigint'),
  'ALTER TABLE adh_decision_advice MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT, MODIFY COLUMN workspace_id BIGINT NOT NULL DEFAULT 0, MODIFY COLUMN created_by BIGINT NOT NULL DEFAULT 0, MODIFY COLUMN updated_by BIGINT NOT NULL DEFAULT 0',
  'SELECT 1');
PREPARE stmt FROM @widen_advice; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @widen_runs := IF(
  EXISTS(SELECT 1 FROM information_schema.COLUMNS
         WHERE TABLE_SCHEMA = 'adh2' AND TABLE_NAME = 'adh_decision_runs'
           AND COLUMN_NAME IN ('tree_id','user_id','workspace_id','conversation_id')
           AND DATA_TYPE <> 'bigint'),
  'ALTER TABLE adh_decision_runs MODIFY COLUMN tree_id BIGINT NOT NULL, MODIFY COLUMN user_id BIGINT NOT NULL DEFAULT 0, MODIFY COLUMN workspace_id BIGINT NOT NULL DEFAULT 0, MODIFY COLUMN conversation_id BIGINT NOT NULL DEFAULT 0',
  'SELECT 1');
PREPARE stmt FROM @widen_runs; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 4) 权限码登记（三个码，读写分离到不同菜单）
--
--    api_pattern 前缀**刻意互不嵌套**：中间件是「任一已授权码命中即放行」，而
--    api_pattern × api_method 的笛卡尔积匹配做不了减法（fnmatch 的 * 连 / 一起吃，
--    `/api/decisions*` 会连带放行 publish）。所以三个出口各占一个路径段：
--      /api/decisions/query*    → decision:read    （含手动评估 POST：它只产实例，不改口径）
--      /api/decisions/config*   → decision:manage  （建树/改草案/建议文案库）
--      /api/decisions/publish*  → decision:publish （发布，独立码 = 两段式第二段）
--    这与内容中心把 publish/reviews 单开前缀的做法同构（见 content_center_migration）。
--
--    【纪律】本批只拥有这三行，不得 UPDATE 任何其它权限点的列（幂等 upsert
--    覆盖别人拥有的列 = 静默拆功能，见 common_pitfalls）。
-- ----------------------------------------------------------------------------
INSERT INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort, is_active, ai_access, ai_action_key, ai_note) VALUES
('decision:read', '查看决策建议', 'workspace', '工作空间',
   '决策建议消费面：树清单/命中历史/建议详情，以及在决策中心手动跑一次完整评估。只消费已发布（active）的决策树，看不到也改不了判断口径',
   '/api/decisions/query*', 'GET,POST', 'workspace:chat', 1091, 1, 'read', 'decision.list,decision.evaluate',
   '只读投影：回命中路径/依据/建议文案，不回 doc 结构与物理名；写口径（建树/发布）不在本码，配成 write 也不会多出任何写能力'),
('decision:manage', '配置决策树', 'data', '数据中台',
   '新建/修改决策树草案与建/改建议动作文案。草案不生效：只落 draft，发布另按 decision:publish',
   '/api/decisions/config*', 'POST,PUT,DELETE', 'data:decisions', 2131, 1, 'none', '',
   '决策树是业务判断口径资产（等同指标口径），改一行就会改变全平台给出的行动建议，必须人工评审后发布，不开放 AI 写'),
('decision:publish', '发布决策树', 'data', '数据中台',
   '把决策树草案发布为生效版本（version +1，重算派生列并做悬空引用门禁）。发布后该树才参与评估',
   '/api/decisions/publish*', 'POST', 'data:decisions', 2132, 1, 'none', '',
   '发布是独立动作 + 独立码（两段式第二段）；与本体/看板发布同口径，不开放 AI 执行')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), module = VALUES(module), module_label = VALUES(module_label),
  description = VALUES(description), api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
  menu_key = VALUES(menu_key), sort = VALUES(sort), is_active = 1,
  ai_access = VALUES(ai_access), ai_action_key = VALUES(ai_action_key), ai_note = VALUES(ai_note);

-- ----------------------------------------------------------------------------
-- 5) 菜单登记（只登记配置面在 data；消费面不占工作空间菜单，见下方下架段）
--
--    配置面为什么放数据中台而不是系统配置（用户 2026-11-12 拍板）：
--    决策树条件引用 `adh_metrics`/`adh_dimensions` 字典，本质是**口径资产**，
--    与「指标与维度」「本体建模」「关系图谱」同族，建模人员日常在数据中台工作。
--    系统配置是一级 AI 模板/凭据域，语义不符。
--
--    首页**不加**决策入口（同轮拍板）：与「默认全关」一致，避免首页区块再做一层
--    显隐而形成双口径。可见入口只有三处 —— Chat 结果卡、看板控件、通知里的实例链接
--    （工作空间侧栏那一项已按 2026-11-12 改拍下架）。
-- ----------------------------------------------------------------------------
INSERT INTO adh_menu_registry (menu_key, label, section, module, sort, perm_code, ai_access, is_active) VALUES
('data:decisions', '决策建议', '数据目录', 'data', 10, 'decision:manage', 'none', 1)
ON DUPLICATE KEY UPDATE
  label = VALUES(label), section = VALUES(section), module = VALUES(module),
  sort = VALUES(sort), perm_code = VALUES(perm_code), ai_access = VALUES(ai_access), is_active = 1;

-- 工作空间侧栏**不放**单独的「决策建议」菜单（2026-11-12 用户改拍，覆盖本批先前的
-- 消费面入口设计）：不是每个问题都需要决策，做成一级入口会暗示“每轮都该看建议”。
-- 消费入口只留三处 —— Chat 结果卡、看板控件、通知里的实例链接；
-- /ws/:id/decisions 路由保留（那是那些链接的落地面），但不再登记为菜单。
-- 这里用 is_active=0 下架而不是 DELETE：菜单行是别人也可能引用的登记面，下架可逆，
-- 且 roles.py 三处读取都带 `WHERE is_active=1`，下架后角色页与实际导航逐项一致。
-- decision:read 的菜单归属同批改挂 workspace:chat（建议卡随问数出现，语义相符），
-- 否则它会变成一个不存在的菜单下的孤儿权限点。
UPDATE adh_menu_registry SET is_active = 0 WHERE menu_key = 'workspace:decisions' AND is_active <> 0;

-- ----------------------------------------------------------------------------
-- 6) 存量角色默认授权：本批**一行都不写**（刻意的例外，不是漏配）
--
--    menu-permission-sync §2.3 要求「存量角色默认授权维持迁移前的入口可见现状」，
--    前提是**迁移前那个入口存在**。决策建议是全新能力面，没有旧现状要维持；
--    而角色门控口径由用户明确拍板为「默认全关，管理员在角色页逐个开通」。
--    同批先例：content_ai_action_migration.sql 也是刻意"存量角色一个都不授"。
--
--    因此这里没有 INSERT INTO adh_role_perms —— 加了就等于擅自扩权。
--    admin 不受影响：/roles/current/permissions 对 admin 走 unrestricted 全放行。
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- 7) 数据中台菜单段与顺序对齐前端导航（显示层纠偏，不碰任何权限语义）
--
--    缺陷（menu-permission-sync §2.1/§6 的现行实例）：`DataPlatformLayout.tsx` 的
--    侧栏把 datasets/ontology/asset-pool/scenario/metrics/knowledge-graph 全放在
--    同一个段名「数据目录」下，而表里登记的是三个不同段（数据源/语义层/数据目录），
--    且 sort 与侧栏顺序不一致 —— 角色页「菜单与功能」会出现三个不存在的分组。
--    不修则本批的 data:decisions 无法在旧结构上取到正确的 section/sort。
--
--    【只改自己拥有的两列】section 与 sort 是显示层排序，改它们不影响任何人的
--    可见范围（可见性真值源是 perm_code ⋈ adh_role_perms）。本段**刻意不动**
--    label / perm_code / ai_access / is_active：
--      - is_active 不动：data:metrics / data:knowledge-graph 当前是 0，即非 admin
--        角色看不到「指标与维度」「关系图谱」侧栏项（已核实的存量缺陷）。把它改回 1
--        会让这两个入口凭空对一批角色出现 = 扩权，必须由人决策，不在本迁移擅自放大。
--      - data:tags / data:glossary / data:quality-rules / data:sync-logs 是已退役行
--        （前端无对应项），保持原样不重排。
-- ----------------------------------------------------------------------------
UPDATE adh_menu_registry SET section = '数据目录', sort = 4  WHERE menu_key = 'data:datasets';
UPDATE adh_menu_registry SET section = '数据目录', sort = 5  WHERE menu_key = 'data:ontology';
UPDATE adh_menu_registry SET section = '数据目录', sort = 6  WHERE menu_key = 'data:asset-pool';
UPDATE adh_menu_registry SET section = '数据目录', sort = 7  WHERE menu_key = 'data:scenario';
UPDATE adh_menu_registry SET section = '数据目录', sort = 8  WHERE menu_key = 'data:metrics';
UPDATE adh_menu_registry SET section = '数据目录', sort = 9  WHERE menu_key = 'data:knowledge-graph';
-- 决策建议插在「关系图谱」之后（sort=10，见 §5），以下三段顺延保持与侧栏同序
UPDATE adh_menu_registry SET section = '数据安全配置', sort = 11 WHERE menu_key = 'data:sensitive';
UPDATE adh_menu_registry SET section = '数据安全配置', sort = 12 WHERE menu_key = 'data:rls';
UPDATE adh_menu_registry SET section = '数据质量', sort = 21 WHERE menu_key = 'data:quality';
UPDATE adh_menu_registry SET section = '数据质量', sort = 22 WHERE menu_key = 'data:quality/rules';
UPDATE adh_menu_registry SET section = '数据质量', sort = 23 WHERE menu_key = 'data:lineage';
UPDATE adh_menu_registry SET section = '数据质量', sort = 24 WHERE menu_key = 'data:standards';
UPDATE adh_menu_registry SET section = '数据同步', sort = 31 WHERE menu_key = 'data:sync';
UPDATE adh_menu_registry SET section = '数据同步', sort = 32 WHERE menu_key = 'data:sync/logs';
UPDATE adh_menu_registry SET section = '数据同步', sort = 33 WHERE menu_key = 'data:udfs';

--    同样地，工作空间侧栏顺序是：数据分析(看板→问数→报表→决策建议) → 云空间(资产清单)
--    → 自动化(任务调度)。新行插 sort=4 会与资产清单撞号（角色页按 (模块, sort) 全局
--    排序，不按分段），所以同批把后两行顺延，使角色页顺序与侧栏逐项一致。
UPDATE adh_menu_registry SET section = '云空间', sort = 5 WHERE menu_key = 'workspace:assets';
UPDATE adh_menu_registry SET section = '自动化', sort = 6 WHERE menu_key = 'workspace:scheduled';

-- ----------------------------------------------------------------------------
-- 8) 菜单权限码回填（只填空，与既有迁移同口径）
-- ----------------------------------------------------------------------------
UPDATE adh_menu_registry mr
  JOIN adh_perm_registry p ON p.menu_key = mr.menu_key AND p.is_active = 1
  SET mr.perm_code = p.perm_code
  WHERE mr.menu_key IN ('workspace:decisions', 'data:decisions')
    AND (mr.perm_code IS NULL OR mr.perm_code = '');

-- ----------------------------------------------------------------------------
-- 9) 核验（应用后人工执行确认，勿删）
-- ----------------------------------------------------------------------------
-- 三张表建出：
-- SHOW TABLES LIKE 'adh_decision%';
-- 权限点与菜单归属（read→workspace:decisions，manage/publish→data:decisions）：
-- SELECT perm_code, label, module, menu_key, api_pattern, api_method, ai_access, ai_action_key
--   FROM adh_perm_registry WHERE perm_code LIKE 'decision:%' ORDER BY sort;
-- 菜单登记与数据中台分段（角色页分组应与 DataPlatformLayout 侧栏逐项一致）：
-- SELECT menu_key, label, section, module, sort, perm_code, is_active
--   FROM adh_menu_registry WHERE module='data' AND is_active=1 ORDER BY sort;
-- 默认全关生效（应 0 行；有行=迁移擅自扩权）：
-- SELECT r.name, rp.perm_code FROM adh_roles r JOIN adh_role_perms rp ON rp.role_id=r.id
--   WHERE rp.perm_code LIKE 'decision:%' ORDER BY r.name;
-- 模拟 analyst 的菜单反查（不应出现 workspace:decisions / data:decisions）：
-- SELECT DISTINCT m.menu_key FROM adh_menu_registry m
--   JOIN adh_role_perms rp ON rp.perm_code = m.perm_code AND rp.role_id=(SELECT id FROM adh_roles WHERE name='analyst')
--   WHERE m.is_active=1 AND m.menu_key LIKE '%decisions%';
-- 双侧一致（ai_action_key 的两个动作必须都存在于 FUNCTION_ACTION_SPECS，
--   由 tests/test_function_ai_access.py::test_registry_matches_db_ai_action_keys 把门）：
-- SELECT perm_code, ai_action_key FROM adh_perm_registry WHERE ai_action_key LIKE 'decision.%';

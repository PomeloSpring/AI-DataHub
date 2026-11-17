-- ============================================================================
-- 双语元数据层（统一双语 JSON 列）— 加列 + 存量确定性英文初值（幂等，可重复执行）
--
-- 背景（用户 2026-11-16 拍板双语体系）:
--   英文界面下用户看到的中文**几乎全部来自后端下发的业务数据**（看板名/图表标题/字模名/
--   AS-BOT 名/栏目名/场景名/技能指令 label）—— 前端语言包 zh/en 已对齐，缺的不是 UI 文案。
--   因此本批给业务数据建双语层：每表加**一列** `i18n` JSON，形如
--     {"name": {"en-US": "ISV Sign Report"}, "description": {"en-US": "..."}}
--   原列（name/label/title/…）**继续存中文**并作为事实源与回落值：
--     * 中文界面 → 读原列（不引入第二个中文事实源）；
--     * 英文界面 → 读 i18n[field]["en-US"]，缺失时回原列，并由响应带 `i18n_missing_fields`
--       显式标注"这条还没英文"（no-silent-degradation：不得让用户误以为"这就是英文版"）。
--   读取口径唯一实现: backend/common/i18n.py（localize_row / localized_text）。
--
-- 形态说明（为什么不是 *_en 列）: 用户选统一双语 JSON 列 —— 第三语言不改表结构。
--   代价与对策: 图表标题/说明卡/参数 label 在 config JSON 深层，双语只能按结构覆盖，
--   故 i18n 另支持 "config" 子对象做**深合并覆盖层**（列表整体替换，字典递归合并），
--   由批量工具与编辑页写入，消费面在组装响应时合并（见 i18n.merge_overlay）。
--
-- 幂等纪律:
--   * ADD COLUMN 走 information_schema 判定 + PREPARE，第二次执行 DO 0；
--   * 英文初值一律带 `JSON_CONTAINS_PATH(...) = 0` 守卫 —— **已有英文不覆盖**，
--     人在页面上改过的译文不会被重跑迁移打回机器值。
--   * 字符串字面量内**不含 ASCII 分号**（logs/apply_migration.py 按分号切句）。
-- ============================================================================

-- ── 1. 加列（7 张表）────────────────────────────────────────────────────
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_dashboards'
      AND COLUMN_NAME = 'i18n') = 0,
  'ALTER TABLE adh_dashboards ADD COLUMN i18n JSON NULL COMMENT ''非默认语言译文 {field: {locale: text}}，原列继续存中文事实源''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_charts'
      AND COLUMN_NAME = 'i18n') = 0,
  'ALTER TABLE adh_charts ADD COLUMN i18n JSON NULL COMMENT ''非默认语言译文，含 config 深合并覆盖层（标题/说明卡/参数 label）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_vis_components'
      AND COLUMN_NAME = 'i18n') = 0,
  'ALTER TABLE adh_vis_components ADD COLUMN i18n JSON NULL COMMENT ''非默认语言译文（字模展示名/描述 + style_config 标签覆盖层）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_as_bots'
      AND COLUMN_NAME = 'i18n') = 0,
  'ALTER TABLE adh_as_bots ADD COLUMN i18n JSON NULL COMMENT ''非默认语言译文（display_name/description）；persona 不动，回复语言由 i18n 统一裁决不在此列''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_categories'
      AND COLUMN_NAME = 'i18n') = 0,
  'ALTER TABLE adh_content_categories ADD COLUMN i18n JSON NULL COMMENT ''非默认语言译文（栏目名/定位副标题）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_content_tag_vocab'
      AND COLUMN_NAME = 'i18n') = 0,
  'ALTER TABLE adh_content_tag_vocab ADD COLUMN i18n JSON NULL COMMENT ''非默认语言译文（标签显示名）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_knowledge_items'
      AND COLUMN_NAME = 'i18n') = 0,
  'ALTER TABLE adh_knowledge_items ADD COLUMN i18n JSON NULL COMMENT ''非默认语言译文（知识/分析场景标题与适用问题）''',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── 2. 栏目英文初值（7 条，人工拟定不用模型：短、术语固定、要一眼对上）────────
-- 写法实测（两个坑都踩过，后人别再踩）：
--   1) JSON_SET 只建**最后一级**——`'$"label"."en-US"'` 在 label 不存在时**静默不写**
--     （实测返 '{}'），不报错最难查。因此改用 JSON_MERGE_PATCH + JSON_OBJECT 嵌套，
--     它按 patch 语义**递归深合并**，已有其他字段/其他语言的译文不受影响。
--   2) JSON_OBJECT 参数个数**必为偶数**（键值成对），奇数直接报 1582；
--     而 logs/apply_migration.py 逐句执行、中途报错时前面已落库，容易出现"上半截成功"。
UPDATE adh_content_categories
   SET i18n = JSON_MERGE_PATCH(COALESCE(i18n, JSON_OBJECT()), JSON_OBJECT('label', JSON_OBJECT('en-US',
       CASE category_key
         WHEN 'about'      THEN 'About Us'
         WHEN 'industry'   THEN 'Dental Industry Trends'
         WHEN 'whitepaper' THEN 'Technical White Papers'
         WHEN 'clinic'     THEN 'Clinical Applications'
         WHEN 'product'    THEN 'Product Updates'
         WHEN 'compliance' THEN 'Compliance and Standards'
         WHEN 'insight'    THEN 'Data Insights'
         ELSE label
       END)))
 WHERE is_active = 1
   AND JSON_CONTAINS_PATH(COALESCE(i18n, JSON_OBJECT()), 'one', '$."label"."en-US"') = 0;

UPDATE adh_content_categories
   SET i18n = JSON_MERGE_PATCH(COALESCE(i18n, JSON_OBJECT()), JSON_OBJECT('description', JSON_OBJECT('en-US',
       CASE category_key
         WHEN 'about'      THEN 'Company business scope, development milestones and outlook (public statements only)'
         WHEN 'industry'   THEN 'Digital dentistry market, chairside restoration, installations and policy trends'
         WHEN 'whitepaper' THEN 'Scanning accuracy, AI modelling, materials, design margins and data interoperability'
         WHEN 'clinic'     THEN 'Implant, orthodontic, restorative, full-arch and dental lab workflows'
         WHEN 'product'    THEN 'Firmware, software, open APIs and accessory releases'
         WHEN 'compliance' THEN 'Device registration, ISO 13485 quality system and imaging data compliance'
         WHEN 'insight'    THEN 'De-identified aggregate reviews of industry and installation data'
         ELSE description
       END)))
 WHERE is_active = 1
   AND description <> ''
   AND JSON_CONTAINS_PATH(COALESCE(i18n, JSON_OBJECT()), 'one', '$."description"."en-US"') = 0;

-- ── 3. 标签词表英文初值（26 条）──────────────────────────────────────────
UPDATE adh_content_tag_vocab
   SET i18n = JSON_MERGE_PATCH(COALESCE(i18n, JSON_OBJECT()), JSON_OBJECT('tag', JSON_OBJECT('en-US',
       CASE tag
         WHEN '扫描精度'    THEN 'Scan Accuracy'
         WHEN '质量控制'    THEN 'Quality Control'
         WHEN '临床操作'    THEN 'Clinical Procedure'
         WHEN '隔湿与止血'  THEN 'Isolation and Hemostasis'
         WHEN '种植与导板'  THEN 'Implant and Surgical Guide'
         WHEN '全口修复'    THEN 'Full-Arch Restoration'
         WHEN '设备管理'    THEN 'Device Management'
         WHEN '失败归因'    THEN 'Failure Attribution'
         WHEN '固件与发布'  THEN 'Firmware and Release'
         WHEN '质量体系'    THEN 'Quality System'
         WHEN '法规与标准'  THEN 'Regulations and Standards'
         WHEN '数据安全'    THEN 'Data Security'
         WHEN '市场与渗透率' THEN 'Market and Penetration'
         WHEN '数据口径'    THEN 'Data Definitions'
         WHEN 'App 使用'    THEN 'App Usage'
         WHEN '自研'        THEN 'In-House App'
         WHEN '渗透率'      THEN 'Penetration Rate'
         WHEN '数据快照'    THEN 'Data Snapshot'
         WHEN '三方集成'    THEN 'Third-Party Integration'
         WHEN '分站点'      THEN 'By Site'
         WHEN '页面上下文'  THEN 'Page Context'
         WHEN '口径手册'    THEN 'Definition Handbook'
         WHEN '指标定义'    THEN 'Metric Definitions'
         WHEN '已知陷阱'    THEN 'Known Pitfalls'
         ELSE tag
       END)))
 WHERE is_active = 1
   AND JSON_CONTAINS_PATH(COALESCE(i18n, JSON_OBJECT()), 'one', '$."tag"."en-US"') = 0;

-- ── 4. AS-BOT 定义英文初值（角色助手名，Chat 侧边栏直接可见）───────────────
UPDATE adh_as_bots
   SET i18n = JSON_MERGE_PATCH(COALESCE(i18n, JSON_OBJECT()),
       JSON_OBJECT('display_name', JSON_OBJECT('en-US',
         CASE as_bot_key
           WHEN 'admin'    THEN 'Admin Assistant'
           WHEN 'analyst'  THEN 'Data Analyst'
           WHEN 'viewer'   THEN 'Viewer Assistant'
           WHEN 'sales2'   THEN 'Sales Assistant'
           ELSE display_name
         END),
       'description', JSON_OBJECT('en-US',
         CASE as_bot_key
           WHEN 'admin'    THEN 'AI assistant for the admin role, covering data analysis and system management'
           WHEN 'analyst'  THEN 'AI assistant for the analyst role, focused on semantic-layer analysis'
           WHEN 'viewer'   THEN 'AI assistant for the viewer role, read-only metadata and metrics'
           WHEN 'sales2'   THEN 'AI assistant for the sales role'
           ELSE description
         END)))
 WHERE (as_bot_key IN ('admin', 'analyst', 'viewer', 'sales2'))
   AND JSON_CONTAINS_PATH(COALESCE(i18n, JSON_OBJECT()), 'one', '$."display_name"."en-US"') = 0;

UPDATE adh_as_bots
   SET i18n = JSON_MERGE_PATCH(COALESCE(i18n, JSON_OBJECT()),
       JSON_OBJECT('display_name', JSON_OBJECT('en-US', 'Operations Manager'),
                   'description', JSON_OBJECT('en-US',
       'Analysis and Q&A partner for front-line operations: business concepts, metric definitions, process SOPs and common questions')))
 WHERE as_bot_key = 'Operations Manager'
   AND JSON_CONTAINS_PATH(COALESCE(i18n, JSON_OBJECT()), 'one', '$."display_name"."en-US"') = 0;

-- ── 5. 分析场景（quick_path）英文初值：`/` 面板候选直接可见 ────────────────
UPDATE adh_knowledge_items
   SET i18n = JSON_MERGE_PATCH(COALESCE(i18n, JSON_OBJECT()),
       JSON_OBJECT('title', JSON_OBJECT('en-US',
       CASE id
         WHEN 1796000000000001 THEN 'Website and H5 Experience and Traffic Analysis'
         WHEN 1796000000000002 THEN 'Correlate User Action Paths by Page URL or IP'
         WHEN 1796000000000003 THEN 'External Partner Integration Analysis (Platform Push and Partner Fetch)'
         ELSE title
       END)))
 WHERE knowledge_type = 'quick_path'
   AND JSON_CONTAINS_PATH(COALESCE(i18n, JSON_OBJECT()), 'one', '$."title"."en-US"') = 0;

UPDATE adh_knowledge_items
   SET i18n = JSON_MERGE_PATCH(COALESCE(i18n, JSON_OBJECT()),
       JSON_OBJECT('scenario', JSON_OBJECT('en-US',
       CASE id
         WHEN 1796000000000001 THEN 'Website and H5 visit volume, UV/PV, session duration, page performance, API success rate, front-end errors and long tasks'
         WHEN 1796000000000002 THEN 'Given a page URL, client IP, session ID or user ID, reconstruct what that subject did on the timeline, including errors and failed API calls'
         WHEN 1796000000000003 THEN 'How external partner systems (ISV or third-party) integrate with the platform: push volume and structure, duplicate pushes, failure layers, partner fetch rankings and action structure, integration status'
         ELSE ''
       END)))
 WHERE knowledge_type = 'quick_path'
   AND id IN (1796000000000001, 1796000000000002, 1796000000000003)
   AND JSON_CONTAINS_PATH(COALESCE(i18n, JSON_OBJECT()), 'one', '$."scenario"."en-US"') = 0;

-- 核验（应用后执行，逐表看列存在 + 英文覆盖率）:
--   SELECT COUNT(*) total, SUM(JSON_CONTAINS_PATH(COALESCE(i18n,'{}'),'one','$.label."en-US"')) en
--     FROM adh_content_categories;

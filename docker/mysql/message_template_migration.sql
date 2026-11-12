-- 模板中心：消息模板建表 + 通知渠道改「引用」+ 菜单改名 + 权限漂移修正（幂等，可重复执行）
--
-- 为什么要改（本批实测缺陷，现库核验）：
--  1) `config.message_template` 是**全平台死字段**：前端在写（NotificationChannels.tsx），
--     后端零消费 —— executor._send_notification 的 docstring 写着"Uses channel's
--     message_template if configured"，实际正文硬编码成"定时分析任务已结束，状态：{status}"。
--     也就是说渠道里填的消息模板一条都没发出去过。引用化之前必须先把这条链接上，
--     否则只是把死字段换成死引用。
--  2) 消息模板手填在每个渠道里 = 同一个文案在 N 个渠道各存一份，改一处漏 N-1 处；
--     现在改为「模板中心」统一维护、渠道按引用选。
--  3) 权限漂移：`report-template:read/manage` 的 api_pattern 绑的是 `/api/report-templates*`，
--     该路径上一批已随双真值源收敛退役；页面实际读 `/api/vis-library*`（受 vis:read/manage 管）。
--     结果角色页勾「查看报告模板」控不住这个页面 —— 属于 menu-permission-sync §2 的空承诺形态。
--     本批把报告模板的读写收口到 `/api/reports/templates*` 并重绑权限，菜单/权限/页面三者对齐。
--
-- 处置：新增 `adh_message_templates`（IM/邮件/短信三类消息内容模板，与报告模板分域：
-- 报告模板是**版式契约**、消息模板是**带变量的文案**）；三条内置模板；登记 message-template:*
-- 权限点并把菜单 `system:report-templates` 改名「模板中心」（menu_key 与 perm_code 不动，
-- 免得存量角色授权集体失效）；把存量渠道里手填的 message_template 一次性沉淀成模板并回填引用。
--
-- 幂等：建表 IF NOT EXISTS；内置模板按 code upsert；权限 ON DUPLICATE KEY UPDATE；
-- 沉淀只在"有内联文本且尚无引用键"时发生，重跑第二次 0 行。

USE adh2;

-- ── 1. 消息模板表 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS adh_message_templates (
  id            BIGINT       NOT NULL AUTO_INCREMENT,
  code          VARCHAR(64)  NOT NULL COMMENT '引用键（渠道 config.message_template_key 存它）',
  name          VARCHAR(128) NOT NULL COMMENT '业务可读名（列表/下拉显示用）',
  template_kind VARCHAR(32)  NOT NULL COMMENT 'im=群机器人/Webhook | email=邮件 | sms=短信',
  format        VARCHAR(16)  NOT NULL DEFAULT 'text' COMMENT 'text/markdown/html（按渠道能力，不得越级）',
  subject       VARCHAR(255) NOT NULL DEFAULT '' COMMENT '邮件主题模板（IM/短信留空）',
  content       TEXT         NOT NULL COMMENT '正文模板，含 {{变量}} 占位；不保存任何业务数值',
  description   VARCHAR(512) NOT NULL DEFAULT '',
  workspace_id  BIGINT       NOT NULL DEFAULT 0 COMMENT '0=全局共享',
  is_active     TINYINT      NOT NULL DEFAULT 1,
  is_builtin    TINYINT      NOT NULL DEFAULT 0 COMMENT '1=内置，admin-only 编辑/删除',
  created_by    BIGINT       NOT NULL DEFAULT 0,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_message_template_code (code),
  INDEX idx_message_template_kind (template_kind, is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ── 2. 内置模板（is_builtin=1，admin-only 可改；变量清单口径见服务端 MESSAGE_TEMPLATE_VARIABLES）──
-- IM：不带 emoji（与 report-delivery 风格基线一致；钉钉/飞书 markdown 也不靠 emoji 表达层级）
INSERT INTO adh_message_templates (code, name, template_kind, format, subject, content, description,
                                   workspace_id, is_active, is_builtin)
VALUES ('mt_builtin_im_default', '任务完成通知（IM）', 'im', 'markdown', '',
        '分析报告已生成\n执行时间：{{date}} {{time}}\n状态：{{status}}\n查询项：共 {{total}}，成功 {{succeeded}}，失败 {{failed}}\n\n登录平台查看完整报告：{{report_link}}',
        '群机器人/Webhook 通用通知：执行时间、状态、查询项计数与需登录的报告链接。', 0, 1, 1)
ON DUPLICATE KEY UPDATE
  -- 内容真变了才抖 updated_at（赋值顺序：这一条必须排在最前，后面比较到的才是原值）：
  -- 模板中心表格要显示「最近修改」，重跑迁移不该把它刷成迁移时间，让人以为文案被改过
  updated_at = IF(adh_message_templates.name <=> VALUES(name)
                  AND adh_message_templates.template_kind <=> VALUES(template_kind)
                  AND adh_message_templates.format <=> VALUES(format)
                  AND adh_message_templates.subject <=> VALUES(subject)
                  AND adh_message_templates.content <=> VALUES(content)
                  AND adh_message_templates.description <=> VALUES(description),
                  adh_message_templates.updated_at, NOW()),
  name = VALUES(name), template_kind = VALUES(template_kind),
  format = VALUES(format), subject = VALUES(subject), content = VALUES(content),
  description = VALUES(description), is_builtin = 1, is_active = 1;

-- 邮件：主题也模板化（邮件渠道此前根本没有主题字段，标题一律是"AI-DataHub Notification"）
INSERT INTO adh_message_templates (code, name, template_kind, format, subject, content, description,
                                   workspace_id, is_active, is_builtin)
VALUES ('mt_builtin_email_default', '分析报告送达（邮件）', 'email', 'markdown',
        '分析报告已生成（{{date}}）',
        '本次分析任务已于 {{date}} {{time}} 结束，状态：{{status}}。\n\n查询项：共 {{total}}，成功 {{succeeded}}，失败 {{failed}}。\n\n查看完整报告：{{report_link}}（需登录平台）',
        '邮件通知通用模板：主题带日期，正文给执行时间、状态、查询项计数与登录后可看的报告链接。', 0, 1, 1)
ON DUPLICATE KEY UPDATE
  -- 内容真变了才抖 updated_at（赋值顺序：这一条必须排在最前，后面比较到的才是原值）：
  -- 模板中心表格要显示「最近修改」，重跑迁移不该把它刷成迁移时间，让人以为文案被改过
  updated_at = IF(adh_message_templates.name <=> VALUES(name)
                  AND adh_message_templates.template_kind <=> VALUES(template_kind)
                  AND adh_message_templates.format <=> VALUES(format)
                  AND adh_message_templates.subject <=> VALUES(subject)
                  AND adh_message_templates.content <=> VALUES(content)
                  AND adh_message_templates.description <=> VALUES(description),
                  adh_message_templates.updated_at, NOW()),
  name = VALUES(name), template_kind = VALUES(template_kind),
  format = VALUES(format), subject = VALUES(subject), content = VALUES(content),
  description = VALUES(description), is_builtin = 1, is_active = 1;

-- 短信：刻意短（国内短信按 70 字计一条），无 emoji、无链接（链接常被运营商拦截且不计入正文口径）
INSERT INTO adh_message_templates (code, name, template_kind, format, subject, content, description,
                                   workspace_id, is_active, is_builtin)
VALUES ('mt_builtin_sms_default', '任务完成提醒（短信）', 'sms', 'text', '',
        '分析报告已于{{date}}生成，状态{{status}}，请登录平台查看。',
        '短信通知模板：控制在 70 字内避免拆分计费；真实发送需服务商签名与已审核模板码。', 0, 1, 1)
ON DUPLICATE KEY UPDATE
  -- 内容真变了才抖 updated_at（赋值顺序：这一条必须排在最前，后面比较到的才是原值）：
  -- 模板中心表格要显示「最近修改」，重跑迁移不该把它刷成迁移时间，让人以为文案被改过
  updated_at = IF(adh_message_templates.name <=> VALUES(name)
                  AND adh_message_templates.template_kind <=> VALUES(template_kind)
                  AND adh_message_templates.format <=> VALUES(format)
                  AND adh_message_templates.subject <=> VALUES(subject)
                  AND adh_message_templates.content <=> VALUES(content)
                  AND adh_message_templates.description <=> VALUES(description),
                  adh_message_templates.updated_at, NOW()),
  name = VALUES(name), template_kind = VALUES(template_kind),
  format = VALUES(format), subject = VALUES(subject), content = VALUES(content),
  description = VALUES(description), is_builtin = 1, is_active = 1;

-- ── 3. 权限点登记（读归消费菜单、写归管理菜单：这里两类都挂「模板中心」菜单）──────
INSERT INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method, menu_key, sort,
   is_active, ai_access, ai_action_key, ai_note) VALUES
  ('message-template:read', '查看消息模板', 'system', '系统配置',
   '查看模板中心的消息模板（IM/邮件/短信）与可用变量清单',
   '/api/message-templates,/api/message-templates/*', 'GET',
   'system:report-templates', 121, 1, 'none', '', ''),
  ('message-template:manage', '管理消息模板', 'system', '系统配置',
   '新建/修改/删除消息模板。模板被通知渠道引用，改了会影响后续所有通知文案',
   '/api/message-templates,/api/message-templates/*', 'POST,PUT,PATCH,DELETE',
   'system:report-templates', 122, 1, 'none', '', '')
ON DUPLICATE KEY UPDATE
  label = VALUES(label), module = VALUES(module), module_label = VALUES(module_label),
  description = VALUES(description), api_pattern = VALUES(api_pattern), api_method = VALUES(api_method),
  menu_key = VALUES(menu_key), sort = VALUES(sort), is_active = 1;

-- ── 4. 修权限漂移：report-template:* 过去绑死路径 /api/report-templates*（已退役）────────
-- 本批把报告模板读写收口到 /api/reports/templates*（内部仍存字模库 report_template，
-- 但页面不再需要 vis:read/manage 才能打开），勾了才打得开、取消才关得掉。
UPDATE adh_perm_registry
   SET api_pattern = '/api/reports/templates,/api/reports/templates/*',
       api_method  = CASE WHEN perm_code LIKE '%:read' THEN 'GET' ELSE 'POST,PUT,PATCH,DELETE' END,
       description = CASE WHEN perm_code LIKE '%:read'
                          THEN '查看报告模板（版式契约：章节 + 图表规格 + 主题）'
                          ELSE '新建/修改/删除报告模板；模板被定时任务与报告中心引用' END,
       label       = '查看报告模板'
 WHERE perm_code = 'report-template:read';
UPDATE adh_perm_registry
   SET api_pattern = '/api/reports/templates,/api/reports/templates/*',
       api_method  = 'POST,PUT,PATCH,DELETE',
       description = '新建/修改/删除报告模板；模板被定时任务与报告中心引用',
       label       = '管理报告模板'
 WHERE perm_code = 'report-template:manage';

-- ── 5. 菜单改名「模板中心」（menu_key 与 perm_code 不动，存量授权继续生效 ）──────────
UPDATE adh_menu_registry
   SET label = '模板中心',
       perm_code = 'report-template:read'
 WHERE menu_key = 'system:report-templates';

-- 同一批对齐的第二处漂移：侧栏把这一页排在「可视化配置」（UI 字模库之后），
-- 登记里却挂「集成配置」—— 角色权限页按登记分组，就会出现“侧栏一个段、角色页另一个段”两张皮。
UPDATE adh_menu_registry
   SET section = '可视化配置',
       sort    = 11
 WHERE menu_key = 'system:report-templates';

-- 通知渠道让位到 12（仍在用户管理 13 之前），否则两段共用一个 sort，角色页顺序不可预期。
UPDATE adh_menu_registry
   SET sort = 12
 WHERE menu_key = 'system:notification-channels';

-- ── 6. 存量角色默认授权：维持"改前能看这个页面的人，改后照样能看"─────────────────
-- 现状是这批人靠 vis:read 打开页面；新码不授权就会出现「菜单改名了、页面读不到模板」。
-- 写权限只跟着原有 report-template:manage 的角色扩散，不由迁移擅自放大。
INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT rp.role_id, 'message-template:read', NOW()
FROM adh_role_perms rp WHERE rp.perm_code = 'report-template:read';

INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT rp.role_id, 'message-template:read', NOW()
FROM adh_role_perms rp WHERE rp.perm_code = 'vis:read';

INSERT IGNORE INTO adh_role_perms (role_id, perm_code, granted_at)
SELECT rp.role_id, 'message-template:manage', NOW()
FROM adh_role_perms rp WHERE rp.perm_code = 'report-template:manage';

-- ── 7. 存量渠道手填文本 → 一次性沉淀成模板 + 回填引用（幂等）──────────────────
-- 只处理"有内联 message_template 且还没有 message_template_key"的行；沉淀后把内联文本
-- 从 config 里删掉（JSON_REMOVE），避免"内联文本 + 引用"两套真值源并存。
-- 注意：接线后这些文案会**第一次真的发出去**，这是修复而非新行为，迁移里显式留下来源标注。
INSERT INTO adh_message_templates
  (code, name, template_kind, format, subject, content, description, workspace_id, is_active,
   is_builtin, created_by)
SELECT CONCAT('mt_channel_', c.id),
       CONCAT(c.name, ' 消息模板'),
       CASE WHEN c.channel_type = 'email' THEN 'email'
            WHEN c.channel_type = 'sms'   THEN 'sms'
            ELSE 'im' END,
       CASE WHEN c.channel_type = 'email' THEN 'text' ELSE 'markdown' END,
       COALESCE(JSON_UNQUOTE(JSON_EXTRACT(c.config, '$.message_subject')), ''),
       JSON_UNQUOTE(JSON_EXTRACT(c.config, '$.message_template')),
       CONCAT('由通知渠道「', c.name, '」的内联消息模板沉淀（模板中心改造一次性迁移）'),
       c.workspace_id, 1, 0, COALESCE(c.owner_id, 0)
  FROM adh_notification_channels c
 WHERE JSON_UNQUOTE(JSON_EXTRACT(c.config, '$.message_template')) IS NOT NULL
   AND TRIM(JSON_UNQUOTE(JSON_EXTRACT(c.config, '$.message_template'))) <> ''
   AND JSON_UNQUOTE(JSON_EXTRACT(c.config, '$.message_template_key')) IS NULL
ON DUPLICATE KEY UPDATE content = VALUES(content), updated_at = NOW();

UPDATE adh_notification_channels c
   SET c.config = JSON_REMOVE(
         JSON_SET(c.config, '$.message_template_key', CONCAT('mt_channel_', c.id)),
         '$.message_template'),
       c.updated_at = NOW()
 WHERE JSON_UNQUOTE(JSON_EXTRACT(c.config, '$.message_template')) IS NOT NULL
   AND TRIM(JSON_UNQUOTE(JSON_EXTRACT(c.config, '$.message_template'))) <> ''
   AND JSON_UNQUOTE(JSON_EXTRACT(c.config, '$.message_template_key')) IS NULL;

-- ── 8. 核验（应用后人工确认，勿删）──────────────────────────────────────
-- 表与内置模板：
--   SELECT code, name, template_kind, format, LEFT(content, 40) FROM adh_message_templates ORDER BY id;
-- 权限点（api_pattern 不得再指向已退役的 /api/report-templates*）：
--   SELECT perm_code, label, menu_key, api_pattern, api_method FROM adh_perm_registry
--    WHERE perm_code LIKE '%template%' ORDER BY perm_code;
-- 菜单（label 应为「模板中心」）：
--   SELECT menu_key, label, section, perm_code, sort FROM adh_menu_registry
--    WHERE menu_key = 'system:report-templates';
-- 存量渠道应只剩引用键、不再有内联文本：
--   SELECT id, name, channel_type,
--          JSON_UNQUOTE(JSON_EXTRACT(config,'$.message_template_key')) AS tpl_key,
--          JSON_UNQUOTE(JSON_EXTRACT(config,'$.message_template'))     AS legacy_text
--     FROM adh_notification_channels;

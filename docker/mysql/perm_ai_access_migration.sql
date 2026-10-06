-- ============================================================================
-- 功能项 AI 能力分级迁移 (幂等, information_schema 守卫)
--
-- 背景: 菜单/权限码背后的平台功能要接入 AI(Waker 可调用), 但不是所有功能都适合
-- 交给 LLM —— 涉密功能(数据源配置/密钥/用户角色权限)不给写权限, 输出含敏感信息的
-- 功能项直接不接 AI。本迁移给权限点注册表加"AI 可调用级别", 作为该边界的唯一真值源。
--
-- 三个新列:
--   ai_access     none=不进LLM工具面 / read=只读可见 / write=可读可写
--   ai_action_key 登记了才生成 LLM 功能工具(空=不接 AI, 只在 UI 上可标级别)
--   ai_note       不开放/只读的原因, 前端必须显示(不可让用户以为"改了就生效")
--
-- 与"系统数据工具"(nl2sql / execute_sql / 知识库检索 / 语义查询)的关系:
--   那批是取数通道, 归 adh_wakers.tools.mcp 逐工具授权 + 数据护城河治理,
--   **不**由本表 ai_access 门控 —— 两个维度正交, 见 perm_link.py 顶部说明。
--
-- 涉密硬约束在代码层(perm_link.SECRET_BOUND_PERMS), 配置不可覆盖:
--   即使把某涉密项的 ai_access 改成 write, 有效级别仍被 cap 到 read/none。
-- ============================================================================

-- ── 1) adh_perm_registry 增列 ───────────────────────────────────────────────
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_perm_registry'
      AND COLUMN_NAME = 'ai_access'
);
SET @ddl = IF(@col_exists = 0,
    'ALTER TABLE adh_perm_registry ADD COLUMN ai_access ENUM(''none'',''read'',''write'') NOT NULL DEFAULT ''none'' COMMENT ''AI(Waker)可调用级别: none=不进LLM工具面/read=只读可见/write=可读可写'' AFTER is_active',
    'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col_exists2 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_perm_registry'
      AND COLUMN_NAME = 'ai_action_key'
);
SET @ddl2 = IF(@col_exists2 = 0,
    'ALTER TABLE adh_perm_registry ADD COLUMN ai_action_key VARCHAR(64) NOT NULL DEFAULT '''' COMMENT ''登记即生成 LLM 功能工具(function_tools.FUNCTION_ACTION_SPECS 的 action_key), 空=不接 AI'' AFTER ai_access',
    'DO 0');
PREPARE stmt FROM @ddl2; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @col_exists3 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_perm_registry'
      AND COLUMN_NAME = 'ai_note'
);
SET @ddl3 = IF(@col_exists3 = 0,
    'ALTER TABLE adh_perm_registry ADD COLUMN ai_note VARCHAR(255) NOT NULL DEFAULT '''' COMMENT ''AI 能力不开放/只读的原因, 前端必须显示'' AFTER ai_action_key',
    'DO 0');
PREPARE stmt FROM @ddl3; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── 2) adh_menu_registry 增列 (菜单级默认值, 供前端批量套用) ─────────────────
SET @col_exists4 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_menu_registry'
      AND COLUMN_NAME = 'ai_access'
);
SET @ddl4 = IF(@col_exists4 = 0,
    'ALTER TABLE adh_menu_registry ADD COLUMN ai_access ENUM(''none'',''read'',''write'') NOT NULL DEFAULT ''none'' COMMENT ''菜单级 AI 默认级别, 仅作前端批量套用的默认值, 有效级别以 adh_perm_registry.ai_access 为准'' AFTER perm_code',
    'DO 0');
PREPARE stmt FROM @ddl4; EXECUTE stmt; DEALLOCATE PREPARE stmt;


-- ============================================================================
-- 3) 种子: 按功能项静态识别分级 (识别口径见 ai_note)
--
--    none  —— 输出含密钥/凭据/个人身份, 或配置本身可被 prompt injection 利用,
--             直接不接 AI(不生成工具, LLM 无从调用)
--    read  —— 读输出可安全脱敏后可见, 禁止一切写
--    write —— 业务/治理类非涉密动作, 可读可写(仍受角色权限码 + 既有 AS-BOT 审批约束)
-- ============================================================================

-- 3.1 none: 涉密配置域 —— 密钥/凭据/身份/权限配置, LLM 不碰
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='输出含连接凭据(主机/账号/密码/连接串), 不开放给 AI'
WHERE perm_code='datasource:manage';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='外部服务接入凭据(App Secret/Token), 不开放给 AI'
WHERE perm_code='mcp:manage';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='模型 API Key/Endpoint 属凭据, 不开放给 AI'
WHERE perm_code='model:manage';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='含账号/个人身份信息, 不开放给 AI'
WHERE perm_code='system:users';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='角色权限配置可被诱导提权, 不开放给 AI'
WHERE perm_code='system:roles';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='行级安全策略可被诱导放宽, 不开放给 AI'
WHERE perm_code='system:rls';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='全局配置含各类密钥, 不开放给 AI'
WHERE perm_code='system:settings';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='Waker 工具授权配置可自提权, 不开放给 AI'
WHERE perm_code='system:wakers';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='执行环境安全配置, 不开放给 AI'
WHERE perm_code='system:sandbox';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='AS-BOT 动作授权配置, 不开放给 AI'
WHERE perm_code='asbot:config';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='系统提示词可被诱导注入, 不开放给 AI'
WHERE perm_code='prompt:manage';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='敏感字段策略配置(屏蔽/脱敏规则), 不开放给 AI'
WHERE perm_code='sensitive:manage';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='通知渠道含 Webhook/SMTP 凭据, 不开放给 AI'
WHERE perm_code='notification:manage';

-- 3.2 read: 读输出可安全脱敏后可见, 禁止一切写
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='datasource.list_safe', ai_note='仅回名称/类型/状态, 不含主机账号连接串; 禁止一切写'
WHERE perm_code='datasource:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='knowledge_base.list', ai_note='仅回知识库名称/类型/状态; 禁止一切写'
WHERE perm_code='kb:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='report.list', ai_note='仅回报表标题/格式/状态, 不含正文与分享令牌; 禁止一切写'
WHERE perm_code='report:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='dashboard.list', ai_note='仅回看板名称/描述/状态; 禁止一切写'
WHERE perm_code='dashboard:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='scheduled_task.list', ai_note='仅回任务名称/周期/状态, 不含 Webhook 密钥与上次错误原文; 禁止一切写'
WHERE perm_code='scheduled:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='quality_rule.list', ai_note='仅回质量规则与通过率统计, 不含失败明细采样(业务数据行); 禁止一切写'
WHERE perm_code='quality:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='dataset.list', ai_note='仅回数据集名称/来源类型/可见性, 不含 SQL 原文; 禁止一切写'
WHERE perm_code='dataset:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回模型名称/状态, 不含 API Key/Endpoint; 禁止一切写'
WHERE perm_code='model:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回元数据业务投影, 物理表列名经脱敏; 禁止一切写'
WHERE perm_code='catalog:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回敏感策略摘要, 不回字段原值; 禁止一切写'
WHERE perm_code='sensitive:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回审计摘要, 不含账号/IP/报错栈; 禁止一切写'
WHERE perm_code='audit:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回用量统计, 不含 SQL/PII 明文; 禁止一切写'
WHERE perm_code='observability:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回健康度快照; 禁止一切写'
WHERE perm_code='monitor:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回对象业务名与别名, 不含物理表列名; 禁止一切写'
WHERE perm_code='ontology:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回指标业务名与口径说明, 不回物理列; 禁止一切写'
WHERE perm_code='metrics:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回术语业务名与释义; 禁止一切写'
WHERE perm_code='glossary:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回本体对象关系业务名; 禁止一切写'
WHERE perm_code='graph:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回表间血缘业务名, 不含物理表名; 禁止一切写'
WHERE perm_code='lineage:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回数据标准名称/口径; 禁止一切写'
WHERE perm_code='standard:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回标签名称/说明; 禁止一切写'
WHERE perm_code='tags:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回同步任务名称/状态, 不含连接凭据; 禁止一切写'
WHERE perm_code='sync:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回 SQL 校验结论, 不执行不返回数据行; 禁止一切写'
WHERE perm_code='playground:read';

-- 3.3 write: 业务/治理类非涉密动作
UPDATE adh_perm_registry SET ai_access='write', ai_action_key='', ai_note='业务分析类非涉密写动作, 仍受角色权限码与 AS-BOT 审批通道约束'
WHERE perm_code IN ('report:manage','dashboard:manage','quality:manage','sync:manage',
                    'tags:manage','standard:manage','kb:manage','dataset:manage');
UPDATE adh_perm_registry SET ai_access='write', ai_action_key='', ai_note='本体建模写动作, 限系统本体域(datasource_id 缺省/0), 走 AS-BOT 审批通道'
WHERE perm_code IN ('ontology:generate','ontology:save','ontology:activate','ontology:import');
UPDATE adh_perm_registry SET ai_access='write', ai_action_key='', ai_note='取数走统一治理入口(execute_query_with_permission), 受数据护城河约束'
WHERE perm_code IN ('dataset:query','playground:execute','scheduled:execute');
UPDATE adh_perm_registry SET ai_access='write', ai_action_key='report.generate', ai_note='生成报表; 输出不含正文以外的凭据字段'
WHERE perm_code='report:manage';
UPDATE adh_perm_registry SET ai_access='write', ai_action_key='scheduled_task.create', ai_note='创建调度任务; Webhook 密钥由服务端生成, 一律不回显'
WHERE perm_code='scheduled:manage';
UPDATE adh_perm_registry SET ai_access='write', ai_action_key='quality_rule.run_check', ai_note='触发质量检核; 仅回通过率统计, 不回失败明细采样'
WHERE perm_code='quality:manage';

-- 3.4 菜单级默认值 (供前端批量套用; 有效级别以 adh_perm_registry.ai_access 为准)
UPDATE adh_menu_registry SET ai_access='none'
WHERE menu_key IN ('system:users','system:roles','system:rls','system:settings','system:wakers','system:sandbox','system:mcp','system:models','system:as-bot');
UPDATE adh_menu_registry SET ai_access='read'
WHERE menu_key IN ('data:datasources','data:tables','data:glossary','data:metrics','data:tags',
                   'data:lineage','data:quality','data:sensitive','data:standards','data:sync',
                   'data:datasets','data:playground','data:ontology');
UPDATE adh_menu_registry SET ai_access='write'
WHERE menu_key IN ('workspace:chat','workspace:reports','workspace:scheduled','system:dashboards');

-- 3.5 存量补全: 上面只覆盖了首批种子的 perm_code/menu_key, 存量库里还有
--     后续迁移引入的权限点与菜单。默认值 fail-closed 是 none(不接 AI),
--     这里逐项显式分类 —— 不靠"默认就安全"含糊过去, 免得只读功能被无谓禁用。

-- 3.5.1 none: 凭据/身份/安全边界/注入面
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='MCP 服务配置含外部接入凭据(App ID/Secret), 不开放给 AI'
WHERE perm_code='mcp:read';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='通知渠道含 Webhook/SMTP 凭据, 不开放给 AI'
WHERE perm_code='notification:read';
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='系统提示词属注入面且不应被 LLM 读取, 不开放给 AI'
WHERE perm_code IN ('prompt:read');
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='技能含 system_prompt(注入面), 不开放给 AI'
WHERE perm_code IN ('skill:manage','skill:read');
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='行级安全策略是安全边界, 可被诱导放宽, 读写均不开放给 AI'
WHERE perm_code IN ('rls:manage','rls:read');
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='角色权限配置可被诱导提权, 读写均不开放给 AI'
WHERE perm_code IN ('role:manage','role:read');
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='含账号/个人身份信息, 读写均不开放给 AI'
WHERE perm_code IN ('user:manage','user:read');
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='全局系统设置含各类密钥, 读写均不开放给 AI'
WHERE perm_code IN ('settings:manage','settings:read');
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='沙箱是执行环境安全边界, 读写均不开放给 AI'
WHERE perm_code IN ('sandbox:manage','sandbox:read');
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='Waker 工具授权配置可自提权, 读写均不开放给 AI'
WHERE perm_code IN ('waker:manage','waker:read');
UPDATE adh_perm_registry SET ai_access='none', ai_action_key='', ai_note='SQL 示例对含物理表/列名, 不开放给 AI(守 §7 黑盒)'
WHERE perm_code IN ('sql-pairs:manage','sql-pairs:read');

-- 3.5.2 read: 信息类只读, 禁止一切写
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回知识管理条目名称/分类; 禁止一切写'
WHERE perm_code='knowledge:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回质量审查结论与统计, 不含审查明细原文; 禁止一切写'
WHERE perm_code='quality-review:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回质量规则名称/类型/阈值, 不含失败明细采样; 禁止一切写'
WHERE perm_code='quality-rules:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回报告模板名称/说明, 不含模板正文; 禁止一切写'
WHERE perm_code='report-template:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回同步执行状态与耗时, 不含连接凭据与报错栈; 禁止一切写'
WHERE perm_code='sync-logs:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回字模组件名称/分类, 不含实现代码; 禁止一切写'
WHERE perm_code='vis:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='仅回元数据业务投影, 物理列名经脱敏; 禁止一切写'
WHERE perm_code='workspace:read';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='AI 助手入口权限, 非具体功能动作, 不生成工具'
WHERE perm_code='asbot:use';
UPDATE adh_perm_registry SET ai_access='read', ai_action_key='', ai_note='智能问答入口权限, 非具体功能动作, 不生成工具'
WHERE perm_code='chat:use';

-- 3.5.3 write: 非涉密治理/运营动作
UPDATE adh_perm_registry SET ai_access='write', ai_action_key='', ai_note='非涉密治理写动作, 仍受角色权限码约束'
WHERE perm_code IN ('catalog:manage','knowledge:manage','quality-review:manage','quality-rules:manage',
                    'report-template:manage','task-monitor:manage','vis:manage','workspace:manage','sync:execute');

-- 3.5.4 菜单级默认值补全
UPDATE adh_menu_registry SET ai_access='none'
WHERE menu_key IN ('data:rls','data:sql-pairs','system:config-versions','system:mcp','system:models',
                   'system:notification-channels','system:rls','system:skills');
UPDATE adh_menu_registry SET ai_access='read'
WHERE menu_key IN ('data:knowledge-graph','data:quality-rules','data:quality/rules',
                   'data:sync-logs','data:sync/logs','system:as-bot','system:audit',
                   'system:knowledge-base','system:knowledge-graph','system:knowledge-management',
                   'system:monitoring','system:observability','system:quality-review',
                   'system:report-templates','system:task-monitor','system:vis-library','system:workspaces');

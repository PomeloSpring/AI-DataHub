-- 权限标识（permission code）模型迁移
-- 用「权限点」统一驱动 菜单可见性 + API 鉴权 + 前端按钮显隐
-- 角色只关联权限码，不再逐个配置菜单/API 路径
--
-- 权限码格式: module:action (如 ontology:generate, datasource:read)
-- 支持通配: module:* 覆盖该模块全部;  admin 角色代码层直接放行全部
-- api_pattern / api_method 支持逗号分隔多值(一个权限码覆盖多段路径/多种方法)
--
-- 注意: 表名用 adh_perm_registry / adh_role_perms, 避开已存在的遗留表
--       adh_permissions(resource/action) 与 adh_role_permissions(遗留漂移)。

USE adh;

-- ============================================================================
-- 1. 权限点注册表
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_perm_registry (
  id INT AUTO_INCREMENT PRIMARY KEY,
  perm_code VARCHAR(64) NOT NULL UNIQUE COMMENT '权限标识, 如 ontology:generate',
  label VARCHAR(128) NOT NULL COMMENT '权限名称',
  module VARCHAR(32) NOT NULL COMMENT '所属模块分组',
  module_label VARCHAR(64) DEFAULT '' COMMENT '模块中文名',
  description VARCHAR(256) DEFAULT '',
  api_pattern VARCHAR(256) DEFAULT '' COMMENT '关联API路径模式(*通配), 空=不做API层鉴权',
  api_method VARCHAR(32) DEFAULT '*' COMMENT 'API方法: GET/POST/PUT/DELETE, 逗号分隔多值',
  menu_key VARCHAR(128) DEFAULT '' COMMENT '关联菜单标识, 空=无对应菜单',
  sort INT DEFAULT 0,
  is_active TINYINT(1) DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='权限点注册表';

-- ============================================================================
-- 2. 角色-权限绑定(按权限码)
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_role_perms (
  id INT AUTO_INCREMENT PRIMARY KEY,
  role_id INT NOT NULL,
  perm_code VARCHAR(64) NOT NULL,
  granted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_perm (role_id, perm_code),
  INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色权限绑定(权限码)';

-- ============================================================================
-- 3. menu_registry 增加 perm_code 列(幂等)
-- ============================================================================
SET @col_exists = (SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_menu_registry' AND COLUMN_NAME='perm_code');
SET @ddl = IF(@col_exists=0,
  'ALTER TABLE adh_menu_registry ADD COLUMN perm_code VARCHAR(64) DEFAULT (\'\')',
  'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ============================================================================
-- 4. 种子: 权限点(模块+动作粒度, 携带 api_pattern 与 menu_key)
-- ============================================================================
INSERT IGNORE INTO adh_perm_registry (perm_code,label,module,module_label,api_pattern,api_method,menu_key,sort) VALUES
-- 数据中台
('datasource:read','查看数据源','data','数据中台','/api/datasources*','GET','data:datasources',101),
('datasource:manage','管理数据源','data','数据中台','/api/datasources*','POST,PUT,DELETE','data:datasources',102),
('catalog:read','查看元数据','data','数据中台','/api/catalog*','GET','data:tables',111),
('playground:execute','SQL查询执行','data','数据中台','/api/playground*,/api/query*','*','data:playground',121),
('ontology:read','查看本体','data','数据中台','/api/catalog/ontology/models*,/api/catalog/ontology/search','GET','data:ontology',131),
('ontology:generate','生成本体','data','数据中台','/api/catalog/ontology/generate*','POST','data:ontology',132),
('ontology:save','保存本体','data','数据中台','/api/catalog/ontology/models*','PUT','data:ontology',133),
('ontology:activate','激活本体','data','数据中台','/api/catalog/ontology/models*','POST','data:ontology',134),
('ontology:import','导入本体YAML','data','数据中台','/api/catalog/ontology/import*','POST','data:ontology',135),
('graph:read','本体可视化','data','数据中台','/api/graph*','GET','data:knowledge-graph',141),
('metrics:read','指标中心','data','数据中台','/api/metrics*','GET','data:metrics',151),
('tags:manage','标签管理','data','数据中台','/api/tags*','*','data:tags',152),
('glossary:read','业务术语','data','数据中台','/api/glossary*,/api/admin/terms*','GET','data:glossary',153),
('quality:read','质量概览','data','数据中台','/api/quality*','GET','data:quality',161),
('quality:manage','质量规则','data','数据中台','/api/quality*','POST,PUT,DELETE','data:quality-rules',162),
('lineage:read','数据血缘','data','数据中台','/api/lineage*','GET','data:lineage',163),
('standard:manage','数据标准','data','数据中台','/api/standards*','*','data:standards',164),
('sensitive:manage','敏感数据治理','data','数据中台','/api/security*','*','data:sensitive',165),
('sync:read','同步任务查看','data','数据中台','/api/sync*','GET','data:sync',171),
('sync:manage','同步任务管理','data','数据中台','/api/sync*','POST,PUT,DELETE','data:sync',172),
-- 工作空间
('chat:use','智能问答','workspace','工作空间','/api/pipeline*,/api/chat/*,/api/agent*,/api/execution*','POST','workspace:chat',201),
('report:read','查看报表','workspace','工作空间','/api/reports*','GET','workspace:reports',211),
('report:manage','管理报表','workspace','工作空间','/api/reports*','POST,PUT,DELETE','workspace:reports',212),
('scheduled:manage','任务调度','workspace','工作空间','/api/scheduled*','*','workspace:scheduled',221),
-- 系统配置
('model:read','查看模型','system','系统配置','/api/model-config*,/api/admin/model-config*','GET','system:models',301),
('model:manage','管理模型','system','系统配置','/api/model-config*,/api/admin/model-config*','POST,PUT,DELETE','system:models',302),
('mcp:manage','MCP服务','system','系统配置','/api/admin/mcp-servers*,/api/mcp*','*','system:mcp',311),
('waker:read','查看Waker','system','系统配置','/api/admin/wakers*,/api/chat/wakers*','GET','system:wakers',321),
('waker:manage','管理Waker','system','系统配置','/api/admin/wakers*','POST,PUT,DELETE','system:wakers',322),
('skill:read','查看技能','system','系统配置','/api/admin/skills*','GET','system:skills',331),
('skill:manage','管理技能','system','系统配置','/api/admin/skills*','POST,PUT,DELETE','system:skills',332),
('asbot:use','使用AI助手','system','系统配置','','','system:as-bot',341),
('asbot:config','配置AI助手','system','系统配置','/api/as-bot/roles*','PUT','system:as-bot',342),
('prompt:manage','Prompt配置','system','系统配置','/api/admin/prompts*','*','system:config-versions',351),
('kb:read','查看知识库','system','系统配置','/api/knowledge-bases*','GET','system:knowledge-base',361),
('kb:manage','管理知识库','system','系统配置','/api/knowledge-bases*','POST,PUT,DELETE','system:knowledge-base',362),
('dashboard:manage','看板管理','system','系统配置','/api/dashboard*,/api/dashboards*','*','system:dashboards',371),
('notification:manage','通知渠道','system','系统配置','/api/notification*','*','system:notification-channels',381),
('report-template:manage','报告模板','system','系统配置','/api/report-templates*','*','system:report-templates',382),
('user:read','查看用户','system','系统配置','/api/users*','GET','system:users',401),
('user:manage','管理用户','system','系统配置','/api/users*','POST,PUT,DELETE','system:users',402),
('workspace:manage','工作空间管理','system','系统配置','/api/workspaces*','*','system:workspaces',411),
('role:read','查看角色','system','系统配置','/api/roles*','GET','system:roles',421),
('role:manage','管理角色权限','system','系统配置','/api/roles*','POST,PUT,DELETE','system:roles',422),
('rls:manage','行级安全','system','系统配置','/api/admin/rls*,/api/roles/rls*','*','system:rls',431),
('audit:read','审计日志','system','系统配置','/api/audit*','GET','system:audit',441),
('monitor:read','系统监控','system','系统配置','/api/monitoring*','GET','system:monitoring',501),
('observability:read','LLM可观测','system','系统配置','/api/observability*','GET','system:observability',502),
('settings:manage','系统设置','system','系统配置','','','system:settings',601);

-- ============================================================================
-- 5. 回填 menu_registry.perm_code(从权限点反查)
-- ============================================================================
UPDATE adh_menu_registry mr
  JOIN adh_perm_registry p ON p.menu_key = mr.menu_key
  SET mr.perm_code = p.perm_code
  WHERE p.menu_key <> '';

-- ============================================================================
-- 6. 迁移旧 role_menus → role_perms(按 menu_key 反查权限码, 仅 is_allowed=1)
-- ============================================================================
INSERT IGNORE INTO adh_role_perms (role_id, perm_code)
SELECT rm.role_id, p.perm_code
FROM adh_role_menus rm
JOIN adh_perm_registry p ON p.menu_key = rm.menu_key
WHERE rm.is_allowed = 1;

-- admin 角色不写绑定(代码层直接放行全部)

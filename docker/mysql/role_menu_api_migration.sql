-- 角色菜单权限 & 接口权限
-- 控制每个角色可访问的前端菜单和后端 API 端点

USE adh;

-- ============================================================================
-- 1. 角色-菜单权限
-- menu_key: 菜单路由标识 (如 '/system/models', '/data/ontology', '/ws/{id}/chat')
-- 使用 section 前缀分组: system:*, data:*, workspace:*
-- 未配置 = 不限制(admin 始终全部可访问)
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_role_menus (
  id INT AUTO_INCREMENT PRIMARY KEY,
  role_id INT NOT NULL,
  menu_key VARCHAR(128) NOT NULL COMMENT '菜单标识(如 system:models, data:ontology)',
  is_allowed TINYINT(1) DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_menu (role_id, menu_key),
  INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色菜单访问权限';

-- ============================================================================
-- 2. 角色-API权限
-- api_pattern: API 路径模式 (如 '/api/pipeline/*', '/api/ontology/models/:id/activate')
-- method: HTTP 方法 (GET/POST/PUT/DELETE/*)
-- 未配置 = 不限制(admin 始终全部可访问)
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_role_apis (
  id INT AUTO_INCREMENT PRIMARY KEY,
  role_id INT NOT NULL,
  api_pattern VARCHAR(256) NOT NULL COMMENT 'API路径模式(支持*通配符)',
  method VARCHAR(10) DEFAULT '*' COMMENT 'HTTP方法: GET/POST/PUT/DELETE/*',
  is_allowed TINYINT(1) DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_api (role_id, api_pattern, method),
  INDEX idx_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='角色API接口访问权限';

-- ============================================================================
-- 3. 预置菜单权限目录(供前端展示可选项)
-- ============================================================================
CREATE TABLE IF NOT EXISTS adh_menu_registry (
  id INT AUTO_INCREMENT PRIMARY KEY,
  menu_key VARCHAR(128) NOT NULL UNIQUE COMMENT '唯一标识',
  label VARCHAR(128) NOT NULL COMMENT '显示名称',
  section VARCHAR(64) DEFAULT '' COMMENT '所属分组',
  module VARCHAR(32) NOT NULL COMMENT '所属模块: system/data/workspace',
  sort INT DEFAULT 0,
  is_active TINYINT(1) DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='系统菜单注册表';

-- 种子: 系统配置菜单
INSERT IGNORE INTO adh_menu_registry (menu_key, label, section, module, sort) VALUES
('system:models', '模型中心', 'AI 模板配置', 'system', 1),
('system:mcp', 'MCP 服务', 'AI 模板配置', 'system', 2),
('system:wakers', 'Waker 配置', 'AI 模板配置', 'system', 3),
('system:skills', '技能配置', 'AI 模板配置', 'system', 4),
('system:as-bot', 'AI 助手', 'AI 模板配置', 'system', 5),
('system:config-versions', 'Prompt配置', 'AI 模板配置', 'system', 6),
('system:knowledge-base', '知识库', '知识管理', 'system', 10),
('system:knowledge-management', '知识管理', '知识管理', 'system', 11),
('system:knowledge-graph', '知识图谱', '知识管理', 'system', 12),
('system:dashboards', '看板管理', '可视化配置', 'system', 20),
('system:notification-channels', '通知渠道', '集成配置', 'system', 30),
('system:report-templates', '报告模板', '集成配置', 'system', 31),
('system:users', '用户管理', '安全与权限', 'system', 40),
('system:workspaces', '工作空间管理', '安全与权限', 'system', 41),
('system:roles', '角色权限', '安全与权限', 'system', 42),
('system:rls', '行级安全', '安全与权限', 'system', 43),
('system:audit', '审计日志', '安全与权限', 'system', 44),
('system:sandbox', '沙箱管理', '运维管理', 'system', 50),
('system:quality-review', '质量审查', '运维管理', 'system', 51),
('system:observability', 'LLM 可观测', '运维管理', 'system', 52),
('system:settings', '系统设置', '系统', 'system', 60),
('system:monitoring', '系统监控', '系统', 'system', 61);

-- 种子: 数据中台菜单
INSERT IGNORE INTO adh_menu_registry (menu_key, label, section, module, sort) VALUES
('data:datasources', '数据源管理', '数据接入', 'data', 1),
('data:tables', '表 & 字段', '数据接入', 'data', 2),
('data:playground', 'SQL Playground', '数据接入', 'data', 3),
('data:ontology', '本体建模', '数据目录', 'data', 10),
('data:knowledge-graph', '本体可视化', '数据目录', 'data', 11),
('data:metrics', '指标中心', '数据目录', 'data', 12),
('data:tags', '标签管理', '数据目录', 'data', 13),
('data:glossary', '业务术语', '数据目录', 'data', 14),
('data:quality', '质量概览', '数据质量', 'data', 20),
('data:quality-rules', '质量规则', '数据质量', 'data', 21),
('data:lineage', '数据血缘', '数据质量', 'data', 22),
('data:standards', '数据标准', '数据质量', 'data', 23),
('data:sensitive', '敏感数据', '数据质量', 'data', 24),
('data:sync', '同步任务', '数据同步', 'data', 30),
('data:sync-logs', '执行日志', '数据同步', 'data', 31);

-- 种子: 工作空间菜单
INSERT IGNORE INTO adh_menu_registry (menu_key, label, section, module, sort) VALUES
('workspace:chat', 'Chat 智能问答', 'AI 分析', 'workspace', 1),
('workspace:reports', '报表中心', 'AI 分析', 'workspace', 2),
('workspace:scheduled', '任务调度', '自动化', 'workspace', 10);

-- ============================================================================
-- 4. 角色默认菜单权限初始化
-- admin(1): 不配置 = 不限制
-- analyst(2): 数据中台全部 + 工作空间全部 + 部分系统
-- viewer(3): 只读数据中台 + Chat
-- ============================================================================

-- analyst: 21个菜单
INSERT IGNORE INTO adh_role_menus (role_id, menu_key, is_allowed) VALUES
(2,'data:datasources',1),(2,'data:tables',1),(2,'data:playground',1),
(2,'data:ontology',1),(2,'data:knowledge-graph',1),(2,'data:metrics',1),(2,'data:tags',1),(2,'data:glossary',1),
(2,'data:quality',1),(2,'data:quality-rules',1),(2,'data:lineage',1),(2,'data:standards',1),(2,'data:sensitive',1),
(2,'data:sync',1),(2,'data:sync-logs',1),
(2,'workspace:chat',1),(2,'workspace:reports',1),(2,'workspace:scheduled',1),
(2,'system:models',1),(2,'system:knowledge-base',1),(2,'system:dashboards',1);

-- viewer: 8个菜单
INSERT IGNORE INTO adh_role_menus (role_id, menu_key, is_allowed) VALUES
(3,'data:tables',1),(3,'data:ontology',1),(3,'data:metrics',1),(3,'data:glossary',1),
(3,'data:quality',1),(3,'data:lineage',1),
(3,'workspace:chat',1),(3,'workspace:reports',1);

-- AS-BOT 系统助手迁移脚本
-- 系统级 AI 助手的角色动作权限与审批记录

-- AS-BOT 角色动作权限: 控制每个角色在 AS-BOT 中可执行的写操作
-- action_key 枚举:
--   'ontology.generate'    — 生成本体草案
--   'ontology.save'        — 保存本体模型编辑
--   'ontology.activate'    — 激活本体模型
--   'ontology.import_yaml' — 导入 Palantir YAML
--   'metadata.sync'        — 同步元数据
CREATE TABLE IF NOT EXISTS adh_as_bot_role_actions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  role_id INT NOT NULL,
  action_key VARCHAR(64) NOT NULL,
  is_allowed TINYINT(1) DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_role_action (role_id, action_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- AS-BOT 审批记录: 所有写操作的审批流水
CREATE TABLE IF NOT EXISTS adh_as_bot_approvals (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  conversation_id BIGINT DEFAULT NULL,
  user_id INT NOT NULL,
  action_key VARCHAR(64) NOT NULL,
  payload JSON COMMENT '待执行的操作参数',
  status ENUM('pending','approved','rejected','executed','failed') DEFAULT 'pending',
  result JSON COMMENT '执行结果',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  decided_at DATETIME DEFAULT NULL,
  decided_by INT DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ============================================================================
-- 系统知识库: qMind (Qoder 云端知识库)
-- AS-BOT 绑定的默认系统知识库, 用于 RAG 检索项目文档与本体知识
-- ============================================================================
INSERT IGNORE INTO adh_knowledge_bases (name, description, kb_type, source_config, status, workspace_ids)
VALUES (
  'AS-BOT 系统知识库',
  'QMind 云端知识库, 包含项目本体模型文档、数据架构说明、本体建模指南等系统级知识。AS-BOT 通过 qmind CLI 进行知识检索。',
  'qmind',
  '{"notebook_id": "system", "description": "AS-BOT 默认系统知识库"}',
  'active',
  '[]'
);

-- ============================================================================
-- 系统级 AS-BOT Waker: 插入全局 Waker 配置 (workspace_id=0)
-- 绑定 qMind 知识库 + ontology-builder / metadata-manager 技能
-- 仅在不存在时插入, 幂等
-- ============================================================================
INSERT IGNORE INTO adh_wakers (
  waker_key, name, display_name, description, system_prompt,
  persona, tools, category, workspace_id, is_active, is_builtin,
  knowledge_base_ids, skills, created_at, updated_at
)
SELECT
  '__system_bot__',
  'as_bot',
  'AS-BOT 系统助手',
  '项目内置 AI 助手, 协助用户构建本体模型和管理元数据。所有写操作需用户审批确认。',
  '你是 AS-BOT，AI-DataHub 数据中台的内置 AI 系统助手。\n\n'
  '## 你的职责\n'
  '1. 协助用户构建和管理本体模型（Ontology）— 生成草案、编辑、激活、YAML导入\n'
  '2. 帮助用户查看和维护元数据 — 表结构、列信息、业务术语、指标定义\n'
  '3. 解答系统功能使用问题 — 模块导航、配置指南、最佳实践\n'
  '4. 依据数据与本体, 用可视化数据大屏(仪表盘)呈现分析结论 — 逐步沟通、选字模组装、确认后创建\n\n'
  '## 工作规则\n'
  '- 取数只走 API 工具，严禁尝试直连数据库\n'
  '- 生成大屏时 widget 只传声明式语义意图(query), 绝不书写裸 SQL\n'
  '- 所有写操作必须先向用户说明操作内容和影响，等待审批卡片确认后才能执行\n'
  '- 不确定的信息通过工具查证，查不到就如实告知，绝不编造\n'
  '- 回答使用中文，简洁专业，结论先行\n'
  '- 遇到超出能力范围的请求，礼貌说明无法处理并指引到正确入口',
  '{"responsibility":"协助用户构建和管理本体模型、查看与维护元数据、引导使用数据中台各模块功能","style":"专业、简洁、引导式。中文回答，术语保留英文，操作前主动说明影响，结论先行，结构化输出","boundary":"不直连数据源;写操作必须用户审批;仅回答系统功能与数据建模相关问题;不臆造数据;不泄露内部实现细节"}',
  '{"groups": ["catalog", "semantic", "ontology", "screen"], "standard": ["read", "grep"], "mcp": {}}',
  'custom',
  0, 1, 1,
  -- 绑定 qMind 系统知识库 (取 adh_knowledge_bases 中 kb_type=qmind 且 name 含 AS-BOT 的 ID)
  (SELECT JSON_ARRAYAGG(id) FROM (
    SELECT id FROM adh_knowledge_bases
    WHERE kb_type = 'qmind' AND name LIKE '%AS-BOT%'
    LIMIT 1
  ) AS kb_ids),
  -- 绑定技能: ontology-builder + metadata-manager + screen-builder
  '["ontology-builder", "metadata-manager", "screen-builder"]',
  NOW(), NOW()
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM adh_wakers WHERE waker_key = '__system_bot__');

-- ============================================================================
-- 已部署库的幂等升级: 为 __system_bot__ 补齐 screen 工具组 + screen-builder 技能
-- (INSERT IGNORE 不会碰已存在记录, 故单独 UPDATE; 仅当缺失时追加)
-- ============================================================================
UPDATE adh_wakers
SET tools = JSON_SET(tools, '$.groups', JSON_ARRAY('catalog', 'semantic', 'ontology', 'screen'))
WHERE waker_key = '__system_bot__'
  AND NOT JSON_CONTAINS(COALESCE(JSON_EXTRACT(tools, '$.groups'), JSON_ARRAY()), '"screen"');

UPDATE adh_wakers
SET skills = JSON_ARRAY_APPEND(skills, '$', 'screen-builder'), updated_at = NOW()
WHERE waker_key = '__system_bot__'
  AND NOT JSON_CONTAINS(COALESCE(skills, JSON_ARRAY()), '"screen-builder"');

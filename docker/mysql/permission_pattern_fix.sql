-- 修正权限点注册表的 api_pattern/api_method 与实际路由的漂移(幂 UPDATE, 可重复执行)
-- 背景: 种子初版按设想路径写的 pattern 与 services/* 真实挂载路径不一致
--   (如本体实际在 /api/catalog/ontology, 看板在 /api/dashboard 等), 会导致管控空转。
-- 配套: api_permission.py 中间件支持 api_pattern / api_method 逗号分隔多值。

-- 列宽适配多方法值(可重复执行)
ALTER TABLE adh_perm_registry MODIFY COLUMN api_method VARCHAR(32) DEFAULT '*' COMMENT 'API方法: GET/POST/PUT/DELETE, 逗号分隔多值';

-- 本体建模: 真实前缀 /api/catalog/ontology (datacatalog)
UPDATE adh_perm_registry SET api_pattern='/api/catalog/ontology/models*,/api/catalog/ontology/search', api_method='GET'  WHERE perm_code='ontology:read';
UPDATE adh_perm_registry SET api_pattern='/api/catalog/ontology/generate*', api_method='POST' WHERE perm_code='ontology:generate';
UPDATE adh_perm_registry SET api_pattern='/api/catalog/ontology/models*', api_method='PUT'   WHERE perm_code='ontology:save';
UPDATE adh_perm_registry SET api_pattern='/api/catalog/ontology/models*', api_method='POST'  WHERE perm_code='ontology:activate';
UPDATE adh_perm_registry SET api_pattern='/api/catalog/ontology/import*', api_method='POST'  WHERE perm_code='ontology:import';

-- 写操作权限补 PUT/DELETE (原只覆盖 POST)
UPDATE adh_perm_registry SET api_method='POST,PUT,DELETE' WHERE perm_code IN
  ('datasource:manage','quality:manage','sync:manage','report:manage','skill:manage',
   'kb:manage','waker:manage','model:manage','user:manage','role:manage');

-- 指标中心: 真实 API /api/metrics (datacatalog)
UPDATE adh_perm_registry SET api_pattern='/api/metrics*', api_method='GET' WHERE perm_code='metrics:read';

-- 业务术语: 真实挂载 /api/admin/terms (datacatalog, vite 亦有 /api/glossary)
UPDATE adh_perm_registry SET api_pattern='/api/glossary*,/api/admin/terms*' WHERE perm_code='glossary:read';

-- 敏感数据治理: 真实路径 /api/security/* (datagov)
UPDATE adh_perm_registry SET api_pattern='/api/security*' WHERE perm_code='sensitive:manage';

-- SQL 执行: 补 /api/query (datamind)
UPDATE adh_perm_registry SET api_pattern='/api/playground*,/api/query*' WHERE perm_code='playground:execute';

-- 智能问答: 补 chat/agent/execution (datamind)
UPDATE adh_perm_registry SET api_pattern='/api/pipeline*,/api/chat/*,/api/agent*,/api/execution*' WHERE perm_code='chat:use';

-- 模型配置: 双服务路径 (datamind /api/model-config + aiplatform /api/admin/model-config)
UPDATE adh_perm_registry SET api_pattern='/api/model-config*,/api/admin/model-config*' WHERE perm_code IN ('model:read','model:manage');

-- MCP: 真实路径 /api/admin/mcp-servers + /api/mcp-market (aiplatform)
UPDATE adh_perm_registry SET api_pattern='/api/admin/mcp-servers*,/api/mcp*' WHERE perm_code='mcp:manage';

-- Waker: 查看补 /api/chat/wakers (datamind 列表)
UPDATE adh_perm_registry SET api_pattern='/api/admin/wakers*,/api/chat/wakers*' WHERE perm_code='waker:read';

-- Prompt 配置: 真实路径 /api/admin/prompts (aiplatform)
UPDATE adh_perm_registry SET api_pattern='/api/admin/prompts*' WHERE perm_code='prompt:manage';

-- 看板: 真实前缀 /api/dashboard (dataviz, 无 s)
UPDATE adh_perm_registry SET api_pattern='/api/dashboard*,/api/dashboards*' WHERE perm_code='dashboard:manage';

-- 通知渠道: 真实前缀 /api/notification (dataflow, 无 s)
UPDATE adh_perm_registry SET api_pattern='/api/notification*' WHERE perm_code='notification:manage';

-- RLS: 补 roles 路由内嵌的 /api/roles/rls* (authservice)
UPDATE adh_perm_registry SET api_pattern='/api/admin/rls*,/api/roles/rls*' WHERE perm_code='rls:manage';

-- 系统设置: 暂无独立 API, 清空 pattern(不受中间件管控)
UPDATE adh_perm_registry SET api_pattern='', api_method='' WHERE perm_code='settings:manage';

-- ES 型数据集迁移（幂等: information_schema 判存后 ALTER + INSERT IGNORE，可重复执行）
-- 1. adh_datasets 增 es_pattern          —— 数据集登记的索引范围（第三种定义类型）
-- 2. 权限码 dataset:es + 菜单归属         —— ES 数据集**配置面**的唯一裁决点
-- 3. 存量角色默认授权：**故意不做**（理由见 §3 注释）
--
-- 口径（与 backend/core/es_client.py、es_tools.py、dataset_service「ES 分支」段一致）：
-- ES 是平台内置只读通道，**不进数据权限治理域**——不是"没接治理"，而是技术上不成立：
-- ES SQL 无 JOIN、只接受可扁平化的子查询（RLS 的包裹注入没有落点），且 sqlglot 无
-- elasticsearch 方言（列级策略的编译期改写没有解析器）。因此闸门收敛为两条：
--   部署侧索引模式白名单（ES_ALLOWED_INDEXES，fail-closed，空白名单=全拒）
--   ∧ 数据集 es_pattern 收窄（正文 FROM 的索引必须落在所选范围内）。
-- 前提：白名单内索引的数据应已脱敏。接入未脱敏索引、或需按角色限制行/列可见性时，
-- 必须重新评估本通道口径，而不是在渲染层或出站层加兜底。

USE adh;

-- ── 1) 索引范围列 ───────────────────────────────────────────────────────────
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasets'
      AND COLUMN_NAME = 'es_pattern') = 0,
  'ALTER TABLE adh_datasets ADD COLUMN es_pattern VARCHAR(256) NOT NULL DEFAULT '''' COMMENT ''ES 型数据集：已选索引模式（data view）范围，空=非 ES 定义'' AFTER object_key',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_datasets'
      AND INDEX_NAME = 'idx_ds_es_pattern') = 0,
  'ALTER TABLE adh_datasets ADD INDEX idx_ds_es_pattern (es_pattern)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- source_type 是 VARCHAR(16) 无 CHECK 约束，取值由服务端 _derive_source_type 派生；
-- 这里只把 COMMENT 补全，避免库里注释与三种类型不一致（不改类型、不动存量行）。
ALTER TABLE adh_datasets
  MODIFY COLUMN source_type VARCHAR(16) NOT NULL DEFAULT 'semantic'
  COMMENT 'semantic|sql|both|es（es=内置 Elasticsearch 通道，不进治理域）';

-- ── 2) 权限码：只 gate 配置面，不 gate 消费面 ───────────────────────────────
-- api_pattern 故意留空（= 不做 API 层路径鉴权），裁决在
-- dataset_service._require_es_config_perm 按码 fail-closed 做。原因：ES 端点与数据集
-- 端点同前缀 `/api/datasets*`，持有 dataset:manage 的角色按路径匹配就会命中 ES 端点，
-- 路径口径分不出"用不用 ES"这件事，写成 api_pattern 反而是个假门。
-- ai_access=none：AS-BOT 访问 ES 走的是「引用内置 MCP 服务 + 逐工具授权」，不由本码
-- 裁决；这里填非 none 就是广告一个代码里不存在的 AI 能力（perm_link.ai_channel 同口径）。
INSERT IGNORE INTO adh_perm_registry
  (perm_code, label, module, module_label, description, api_pattern, api_method,
   menu_key, sort, ai_access, ai_note) VALUES
('dataset:es', '使用 Elasticsearch 数据集', 'data', '数据中台',
 '新建/修改 ES 型数据集、查看索引范围与字段、校验 ES 正文。ES 通道不进列级/行级数据权限治理，可查范围由部署侧索引白名单与数据集所选范围决定；已保存的 ES 数据集图表仍按 dataset:read + 看板可见性渲染，本码只把关配置面。',
 '', '*', 'data:datasets', 144, 'none',
 'AI 侧访问 ES 走「引用内置 MCP 服务 + 逐工具授权」，不由本码裁决；本码只控制数据集配置页面。');

-- 回填菜单权限码关联（与 datasets_migration 同写法，保证 menu_registry 一致）
UPDATE adh_menu_registry mr
  JOIN adh_perm_registry p ON p.menu_key = mr.menu_key
  SET mr.perm_code = p.perm_code
  WHERE p.menu_key <> '' AND (mr.perm_code IS NULL OR mr.perm_code = '');

-- ── 3) 存量角色默认授权：故意不做 ───────────────────────────────────────────
-- menu-permission-sync §2.3 要求"新菜单对应的权限点默认授权给既有角色"，其目的是
-- 防止**存量入口凭空消失**。ES 型数据集在本次改动前不存在，没有任何用户拥有过这个
-- 入口，因此不授权才是"维持迁移前现状"；反之默认发给 analyst 等角色，等于未经要求
-- 就把一条未套行列级策略的数据通道开放出去。
-- 需要开放时：在「角色权限 → 菜单与功能 → 数据中台/数据集」勾选「使用 Elasticsearch 数据集」。
-- （admin 不受影响：配置面按 check_api_permission 的 admin 放行 + 本码检查的 admin 旁路。）

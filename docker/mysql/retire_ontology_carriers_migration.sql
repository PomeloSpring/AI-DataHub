-- ============================================================================
-- 彻底下线取数路径（carrier）：删事实源表 + 指标约束列 + AS-BOT 工具引用（幂等，可重复执行）
--
-- 背景（决策已拍板）：跨源拓扑/承载（carrier）能力移除。全球/多站点统一由分析侧
--   按站点逐站编排、分站呈现（不跨站求和）；跨源去重/汇总口径改为在知识库文档描述。
--   代码侧 backend/semantics/topology.py、REST /models/{id}/carriers、
--   SDK 工具 list_ontology_carriers/save_ontology_carrier、binding_resolver 择路均已删除。
--   本文件收口数据库侧：DROP 事实源表 + 删指标列 + 清理 AS-BOT 的 tool 引用（防"配了没用"的死工具）。
--
-- 历史迁移文件 ontology_carrier_migration.sql / ontology_carrier_routing_migration.sql
--   保留（forward-only 记录既往建表），本文件在其之后执行即可。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. 删除承载事实源表（含其上的索引/唯一键随表一并消失）
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS adh_ontology_carriers;

-- ----------------------------------------------------------------------------
-- 2. 删除指标层的取数路径约束列 allowed_carrier（幂等：经 information_schema 守卫）
-- ----------------------------------------------------------------------------
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_metrics'
      AND COLUMN_NAME = 'allowed_carrier') > 0,
  'ALTER TABLE adh_metrics DROP COLUMN allowed_carrier',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 3. AS-BOT 工具清单：移除已删除的两个工具引用（menu-permission-sync：配了没用即死引用）
-- ----------------------------------------------------------------------------
-- 从 adh_as_bots.tools 的 $.mcp.ontology 数组里剔除 list_ontology_carriers / save_ontology_carrier。
--   逐条单独 UPDATE（每次在当前文档上重新 JSON_SEARCH 定位，避免一次删多元素时的下标位移），
--   WHERE 用 JSON_SEARCH IS NOT NULL 守卫 → 不存在即 no-op，幂等且覆盖所有 bot 行。
UPDATE adh_as_bots
   SET tools = JSON_REMOVE(
         tools,
         JSON_UNQUOTE(JSON_SEARCH(tools, 'one', 'list_ontology_carriers', NULL, '$.mcp.ontology[*]')))
 WHERE JSON_SEARCH(tools, 'one', 'list_ontology_carriers', NULL, '$.mcp.ontology[*]') IS NOT NULL;

UPDATE adh_as_bots
   SET tools = JSON_REMOVE(
         tools,
         JSON_UNQUOTE(JSON_SEARCH(tools, 'one', 'save_ontology_carrier', NULL, '$.mcp.ontology[*]')))
 WHERE JSON_SEARCH(tools, 'one', 'save_ontology_carrier', NULL, '$.mcp.ontology[*]') IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 4. 权限码核验（carrier 端点从未登记独立权限码，读写复用 /api/catalog/ontology/models* 的
--    ontology:read/ontology:save pattern 覆盖，因此无死权限码需要删除；此处仅核验不残留）。
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- 5. 核验（人工看输出，不自动改数据）
-- ----------------------------------------------------------------------------
-- 表已消失：
--   SELECT COUNT(*) AS carriers_table FROM information_schema.TABLES
--    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_ontology_carriers';         -- 期望 0
-- 列已消失：
--   SELECT COUNT(*) AS allowed_carrier_col FROM information_schema.COLUMNS
--    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_metrics' AND COLUMN_NAME='allowed_carrier'; -- 期望 0
-- 工具引用已清理（期望无命中）：
--   SELECT id, name FROM adh_as_bots WHERE tools LIKE '%ontology_carrier%';

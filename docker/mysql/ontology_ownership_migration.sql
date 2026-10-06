-- 本体归属改造：从「按数据源」到「按业务域」（幂等，可重复执行）
--
-- 为什么改：本体是业务语义层，不该被物理拓扑（数据源连接）切割。
-- 之前 `adh_ontology_models.datasource_id` 是归属键，导致：
--   ① 业务域被物理边界切割（test-alb-全量本体里混了客户/案例/订单 6 个域）
--   ② 跨源业务概念无法建模（"客户"只能建在一个源下）
--   ③ 归属键是雪花 id，数据源重建即失效
--
-- 双轨设计：
--   kind='source'   源本体：该源核心业务表的语义标注，服务 nl2sql 选表/数据地图
--   kind='business' 业务本体：跨源业务对象+口径，服务 LLM grounding/ChatBI（归属键 = domain）
--   kind='system'   系统本体：平台自身元数据（AS-BOT 用）
-- datasource_id 降级为「源本体的物理来源」，不再是归属键。

USE adh2;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_ontology_models'
      AND COLUMN_NAME = 'kind') = 0,
  'ALTER TABLE adh_ontology_models ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT ''business'' COMMENT ''source|business|system'' AFTER name',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_ontology_models'
      AND COLUMN_NAME = 'domain') = 0,
  'ALTER TABLE adh_ontology_models ADD COLUMN domain VARCHAR(64) NOT NULL DEFAULT '''' COMMENT ''业务域(business 本体的归属键, 与 adh_tag_values「业务域」同口径)'' AFTER kind',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_ontology_models'
      AND INDEX_NAME = 'idx_om_kind_domain') = 0,
  'ALTER TABLE adh_ontology_models ADD INDEX idx_om_kind_domain (kind, domain)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 存量定性：datasource_id=0/null = 系统本体；其余 = 源本体（业务本体由后续拆分产生）
UPDATE adh_ontology_models SET kind = 'system'
WHERE COALESCE(datasource_id, 0) = 0 AND kind = 'business';
UPDATE adh_ontology_models SET kind = 'source'
WHERE COALESCE(datasource_id, 0) > 0 AND kind = 'business';

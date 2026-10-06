-- 数据产品绑定列迁移（幂等: DDL 经 information_schema 守卫，可重复执行）
--
-- 为什么把 product_ref 落进 adh_ontology_bindings，而不是只在解析时现查：
--   P3 的 schema 变更影响分析要能回答「契约变了，哪些本体对象/指标/看板受影响」。
--   若 product_ref 只是解析时的临时补齐，就无法从产品反查到绑定它的本体对象。
--   落成持久化列后：`SELECT object_key FROM adh_ontology_bindings WHERE product_ref = ?`
--   一条 SQL 就能定位影响面。
--
-- 职责：product_ref 是**稳定身份**（如 `test-alb.t_case_records`，不含内部 id）；
--      物理定位（catalog_ref/physical_table）仍是它的属性，两者都留。

USE adh2;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_ontology_bindings'
      AND COLUMN_NAME = 'product_ref') = 0,
  'ALTER TABLE adh_ontology_bindings ADD COLUMN product_ref VARCHAR(128) NOT NULL DEFAULT '''' COMMENT ''数据产品名(稳定身份, 不含内部 id); 空=该绑定未挂到数据产品'' AFTER datasource_name',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_ontology_bindings'
      AND INDEX_NAME = 'idx_ob_product_ref') = 0,
  'ALTER TABLE adh_ontology_bindings ADD INDEX idx_ob_product_ref (product_ref)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 存量行回填：按 (datasource_name, physical_table) 关联数据产品
-- 匹配口径与 data_product_service.find_by_table 一致（ds 为空时回落只按表名）
UPDATE adh_ontology_bindings b
JOIN adh_data_products p
  ON p.physical_table = b.physical_table
 AND (p.datasource_name = b.datasource_name OR b.datasource_name = '' OR p.datasource_name = '')
SET b.product_ref = p.product_name
WHERE b.status = 'active' AND b.product_ref = '';

-- ── 契约变更审批：候选版本状态列 ─────────────────────────────────
-- 破坏性变更（删列/类型变更）不自动生效，先记 pending 候选版本，
-- 走 AS-BOT Action `product.contract_change` 审批通过后才 applied。
SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_data_product_versions'
      AND COLUMN_NAME = 'status') = 0,
  'ALTER TABLE adh_data_product_versions ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT ''applied'' COMMENT ''applied|pending: 破坏性变更挂起待审批'' AFTER is_breaking',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

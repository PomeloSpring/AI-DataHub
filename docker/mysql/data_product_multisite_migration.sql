-- 数据产品多站点支持（幂等: DDL 经 information_schema 守卫，可重复执行）
--
-- 为什么需要 product_class：6 个站点同构（表结构一样）时，它们是**同一个产品类的
-- 6 个物理部署**，不是 6 个无关的表。若无产品类，schema 契约会退化成 6 份独立契约，
-- 出现「改了北京的列、忘了上海」——同构约束无法校验。
--   product_class = 产品类（逻辑表名，如 `t_case_records`），同类站点共享一份 schema 契约；
--   product_name  = 物理部署实例（`site-bj.t_case_records`），每站点一个，各有 owner/SLA。
-- 本体对象绑 product_class（与站点无关的业务概念），执行时按会话站点展开到具体实例。
--
-- site 列：站点业务名（北京站/上海站…），空 = 非站点化部署（单站点/平台表）。
-- 站点清单的事实源是维度字典 value_labels（ontology-modeling §3），此处只存归属。
--
-- 职责划分回顾：
--   adh_data_products.product_class = 一份契约（×1）
--   adh_data_products(site)         = N 个物理实例（×6）
--   adh_ontology_bindings           = 对象 → 实例 的站点路由（每站点一条）

USE adh2;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_data_products'
      AND COLUMN_NAME = 'product_class') = 0,
  'ALTER TABLE adh_data_products ADD COLUMN product_class VARCHAR(128) NOT NULL DEFAULT '''' COMMENT ''产品类：同构站点共享的 schema 契约名（逻辑表名）'' AFTER product_name',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_data_products'
      AND COLUMN_NAME = 'site') = 0,
  'ALTER TABLE adh_data_products ADD COLUMN site VARCHAR(64) NOT NULL DEFAULT '''' COMMENT ''站点业务名；空=非站点化部署'' AFTER product_class',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_data_products'
      AND INDEX_NAME = 'idx_dp_class') = 0,
  'ALTER TABLE adh_data_products ADD KEY idx_dp_class (product_class, status)',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 存量回填：product_class 取物理表名（非站点化部署的类名即表名），site 暂空
UPDATE adh_data_products SET product_class = physical_table
 WHERE COALESCE(product_class, '') = '' AND COALESCE(physical_table, '') <> '';

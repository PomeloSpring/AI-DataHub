-- 血缘资产类型扩展（幂等: DDL 经 information_schema 守卫）
--
-- 为什么要扩：P5 资产血缘贯通要画「物理表→数据产品→本体对象→口径→数据集→图表」，
-- 而 node_type 原 enum 只有 table/column/etl_job/report/metric，装不下产品/本体对象/数据集/图表。
-- 用现有 enum 近似（product→table、dataset→report）会让血缘语义混乱，故显式扩展。
--
-- 节点类型 = 资产层；边类型 = 资产关系（谁产出/绑定/定义/消费/可视化）。

USE adh2;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_lineage_nodes'
      AND COLUMN_NAME='node_type'
      AND COLUMN_TYPE LIKE '%product%') = 0,
  'ALTER TABLE adh_lineage_nodes MODIFY COLUMN node_type ENUM(''table'',''column'',''etl_job'',''report'',''metric'',''product'',''ontology_object'',''dimension'',''dataset'',''chart'') NOT NULL',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @ddl = IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='adh_lineage_edges'
      AND COLUMN_NAME='edge_type'
      AND COLUMN_TYPE LIKE '%produces%') = 0,
  'ALTER TABLE adh_lineage_edges MODIFY COLUMN edge_type ENUM(''transform'',''derive'',''join'',''aggregate'',''filter'',''produces'',''binds_to'',''defines'',''consumed_by'',''visualizes'') NOT NULL',
  'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

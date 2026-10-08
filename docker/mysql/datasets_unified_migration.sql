-- ============================================================================
-- 数据集统一改造迁移 (幂等, information_schema 守卫)
--
-- 背景: 语义对象与 SQL 不是二选一 —— SQL 是查询的实际执行, 语义对象供语义层检索,
-- 两者可互相转换(compile_from_semantic / extract_from_sql)。可见性功能整体退役。
--
-- 1. adh_datasets.drop visibility  (可见性功能移除, 越权防护由取数治理入口承担)
-- 2. adh_datasets.chart_type       预设图表类型(看板"从数据集引入"直接采用)
-- 3. adh_datasets.chart_preset     预设字段映射 {xCol,yCol,groupCol,limit} 与图表 config 对齐
--
-- source_type 列保留兼容存量数据, 但应用逻辑不再按它二选一(branch by object_key/sql_query)。
-- 注意: 应用侧 business_asset_ontology._collect / dataset_service / function_tools /
-- report_service 已同批移除 visibility 读写, 本迁移与代码须一起部署。
-- ============================================================================

USE adh2;

-- ── 1) 删除 visibility 列(存在才删) ──────────────────────────────────────────
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_datasets'
      AND COLUMN_NAME = 'visibility'
);
SET @ddl = IF(@col_exists > 0,
    'ALTER TABLE adh_datasets DROP COLUMN visibility',
    'DO 0');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ── 2) chart_type 列(预设图表类型) ───────────────────────────────────────────
SET @col_exists2 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_datasets'
      AND COLUMN_NAME = 'chart_type'
);
SET @ddl2 = IF(@col_exists2 = 0,
    'ALTER TABLE adh_datasets ADD COLUMN chart_type VARCHAR(32) NOT NULL DEFAULT '''' COMMENT ''预设图表类型(bar/line/pie...), 看板引入时直接采用'' AFTER field_config',
    'DO 0');
PREPARE stmt2 FROM @ddl2; EXECUTE stmt2; DEALLOCATE PREPARE stmt2;

-- ── 3) chart_preset 列(预设字段映射) ─────────────────────────────────────────
SET @col_exists3 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'adh_datasets'
      AND COLUMN_NAME = 'chart_preset'
);
SET @ddl3 = IF(@col_exists3 = 0,
    'ALTER TABLE adh_datasets ADD COLUMN chart_preset JSON NULL COMMENT ''预设字段映射 {xCol,yCol,groupCol,limit}, 引入看板时搬入 chart.config'' AFTER chart_type',
    'DO 0');
PREPARE stmt3 FROM @ddl3; EXECUTE stmt3; DEALLOCATE PREPARE stmt3;

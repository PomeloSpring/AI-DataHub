-- ═══════════════════════════════════════════════════════════════
-- 语义字典增强迁移: 维度/指标别名 + 枚举值业务标签
--   adh_dimensions: + aliases JSON, + value_labels JSON
--   adh_metrics:    + aliases JSON
-- 用途: 语义层按别名解析维度名("创建时间"→创建日期),
--       枚举码值在查询结果中直出业务名(case_status 3→"已上传")。
-- 幂等: information_schema 判断后再 ALTER, 可重复执行。
-- ═══════════════════════════════════════════════════════════════

-- adh_dimensions.aliases
SET @exist := (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_dimensions' AND COLUMN_NAME = 'aliases');
SET @sql := IF(@exist = 0,
    'ALTER TABLE adh_dimensions ADD COLUMN aliases JSON COMMENT ''维度别名列表, 如 ["创建时间","case created date"]'' AFTER description',
    'SELECT ''adh_dimensions.aliases already exists''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- adh_dimensions.value_labels
SET @exist := (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_dimensions' AND COLUMN_NAME = 'value_labels');
SET @sql := IF(@exist = 0,
    'ALTER TABLE adh_dimensions ADD COLUMN value_labels JSON COMMENT ''枚举码值->业务标签, 如 {"0":"仅表单","1":"待处理"}'' AFTER aliases',
    'SELECT ''adh_dimensions.value_labels already exists''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- adh_metrics.aliases
SET @exist := (SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_metrics' AND COLUMN_NAME = 'aliases');
SET @sql := IF(@exist = 0,
    'ALTER TABLE adh_metrics ADD COLUMN aliases JSON COMMENT ''指标别名列表, 如 ["口扫案例数","total_cases"]'' AFTER description',
    'SELECT ''adh_metrics.aliases already exists''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ============================================================================
-- adh_llm_models.model_id 给默认值（修「模型中心 · 添加模型」必现 500）
--
-- 背景：init.sql 建表时 `model_id VARCHAR(100) NOT NULL` 无默认值，而该列**没有任何
--       消费方**（代码里出现的 model_id 全是路由参数名），前端表单也没有该字段，
--       实际模型标识由后补的 `model_name` 承担。结果是任何不带 model_id 的 INSERT
--       都被 MySQL 拒绝：ERROR 1364 "Field 'model_id' doesn't have a default value"
--       ——「模型中心 · 大模型配置」点保存必然失败，表长期为空。
--
-- 处置：列保留（不删，避免历史备份/外部脚本引用断裂），改为可空并给空串默认值，
--       使"没填"成为合法状态；写入侧同时显式带 model_id（值=model_name），
--       让该列在有默认值的前提下仍保持语义一致。
--
-- 幂等：MySQL 的 MODIFY COLUMN 不支持 IF NOT EXISTS，先查 information_schema 判定
--       当前是否仍为 NOT NULL 且无默认值，重复执行安全。
-- ============================================================================

SET @col_nullable = (
    SELECT IS_NULLABLE FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_llm_models' AND COLUMN_NAME = 'model_id'
);
SET @col_default = (
    SELECT COLUMN_DEFAULT FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'adh_llm_models' AND COLUMN_NAME = 'model_id'
);
SET @ddl = IF(@col_nullable IS NOT NULL AND @col_nullable = 'NO' AND @col_default IS NULL,
    'ALTER TABLE adh_llm_models MODIFY COLUMN model_id VARCHAR(100) NULL DEFAULT '''' COMMENT ''历史列：供应商侧模型标识，与 model_name 同值；实际消费方为 model_name''',
    'SELECT 1');
PREPARE stmt FROM @ddl;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 历史行（若有）补齐 model_id = model_name，避免同一概念两处一空一有
UPDATE adh_llm_models SET model_id = COALESCE(NULLIF(model_id, ''), model_name, '')
WHERE model_id IS NULL OR model_id = '';

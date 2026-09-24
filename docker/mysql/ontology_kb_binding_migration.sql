-- 业务本体「按模型选择目标知识库」地基
-- 现状：本体→qmind 同步目标只认全局 source_config.sync_ontology=true 的库(唯一命中系统知识库)，
-- 导致业务本体被推到「AI-DataHub 系统知识库」。改为业务模型可在模型工作区显式选定目标库。
-- kb_id：adh_ontology_models 的行级配置列(非派生表)，记录业务本体同步去向的 qmind 知识库 id。
--   NULL/0 = 未绑定 → 业务本体不同步并显式提示；系统本体(datasource_id 0/null)不写此列，沿用全局 sync_ontology。
-- 幂等：列存在则跳过。

SET @schema := DATABASE();
SET @has_col := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = @schema
      AND TABLE_NAME = 'adh_ontology_models'
      AND COLUMN_NAME = 'kb_id'
);
SET @ddl := IF(@has_col = 0,
    'ALTER TABLE adh_ontology_models ADD COLUMN kb_id BIGINT NULL DEFAULT NULL COMMENT ''业务本体同步目标知识库(adh_knowledge_bases.id, qmind); 系统本体为 NULL 沿用全局 sync_ontology''',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 索引：按 kb_id 反查绑定该库的模型(下线/对账用)
SET @has_idx := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = @schema
      AND TABLE_NAME = 'adh_ontology_models'
      AND INDEX_NAME = 'idx_ontology_models_kb'
);
SET @ddl2 := IF(@has_idx = 0,
    'ALTER TABLE adh_ontology_models ADD INDEX idx_ontology_models_kb (kb_id)',
    'SELECT 1');
PREPARE stmt FROM @ddl2; EXECUTE stmt; DEALLOCATE PREPARE stmt;

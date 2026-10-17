-- ============================================================================
-- 预制 SQL 收敛至数据集（adh_datasets）迁移
--
-- 背景：平台内"可信任预制 SQL"曾分散在两处 —— 语义层模板表 adh_sql_templates
--       （bind_kind='sql_template'，实际 0 绑定、仅检索喂素材，且曾把裸 SQL 泄给 LLM）
--       与字模库 adh_vis_components(category='sql_template')（2 条 builtin 演示片段，
--       `{{table}}` 占位不可执行，用户自建 0 条）。两者都退役，能力并入数据集：
--         adh_datasets = 唯一承载：参数化 + 多方言正文 + 字段声明(下钻) + 治理执行。
--
-- 本迁移做的事（幂等，不含 USE，用 `mysql ... <METADATA_DB_DATABASE> < 本文件` 施加）：
--   1. adh_datasets 扩列：variables(参数声明) / sql_bodies(多方言正文) /
--      touched_tables(静态提表，服务端计算) / certified / approved_by；
--   2. **数据搬迁**：adh_sql_templates 的正文/变量/描述/数据源归属 → adh_datasets
--      （按 template_name 去重，已存在同名数据集则跳过；字典与引用不丢）；
--   3. adh_ontology_bindings：template_ref → dataset_ref（改名，语义升级为
--      "指向 adh_datasets.name"——用 name 而非 id，删除重建后仍可关联）；
--   4. 字模库退役 sql_template 分类：删 2 条 builtin 演示片段 + DROP query_template 列
--      （该列是假功能：params/bindTable 全库无消费方，`{{}}` 语法不可执行）；
--   5. DROP TABLE adh_sql_templates（表、代码、注释一并清干净，不留空壳）。
--
-- 刻意不做的两件事：
--   * 不自动改写搬迁行的 datasource_id —— user_path_conversion_rate 指向的
--     数据源 id 已不存在（现存 7 个源里没有），归属需业务确认后再改，迁移不猜；
--   * 不自动补 touched_tables —— 该列由服务端在保存/编译时静态提取（运行期也有
--     兜底提取），SQL 里算不出来，置 NULL 即可，编译器会当场提取。
--
-- 顺序敏感：2/3/4 在 DROP 之前，先保住数据再拆表。
-- ============================================================================

DELIMITER $$
DROP PROCEDURE IF EXISTS adh_ds_add_column$$
CREATE PROCEDURE adh_ds_add_column(IN tbl VARCHAR(64), IN col VARCHAR(64), IN definition TEXT)
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema=DATABASE() AND table_name=tbl)
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_schema=DATABASE() AND table_name=tbl AND column_name=col) THEN
        SET @ddl=CONCAT('ALTER TABLE `', tbl, '` ADD COLUMN `', col, '` ', definition);
        PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

-- ----------------------------------------------------------------------------
-- 1. adh_datasets 扩列（预制 SQL 的声明层）
-- ----------------------------------------------------------------------------
-- 参数声明 {"start_date": {"type":"date"}, "channel": {"type":"enum","values":[...]}}
-- 编译期按类型严格校验 + 拒危险片段后渲染为字面量（SLS 的 PG 端点不支持
-- prepared statement，所以必须服务端字面量渲染，不能改成绑定参数）。
CALL adh_ds_add_column('adh_datasets', 'variables',
    "JSON NULL COMMENT '参数声明 {name: {type,values,default}}; ${name} 占位符按类型渲染'");

-- 多方言正文 {"doris": "...", "mysql": "...", "postgres": "...", "sls": "..."}
-- 编译时按 引擎 → 方言族 → generic 取用；缺本引擎实现即拒绝执行（不回退他方言）。
CALL adh_ds_add_column('adh_datasets', 'sql_bodies',
    "JSON NULL COMMENT '按引擎/方言族注册的正文; 缺本引擎实现即拒绝执行'");

-- 注册时静态提取的物理表清单：供语义层 RLS 改写与敏感策略加载（护栏 §4/§10）。
CALL adh_ds_add_column('adh_datasets', 'touched_tables',
    "JSON NULL COMMENT '正文触及的物理表(保存/编译时静态提取), 供 RLS/敏感策略消费'");

CALL adh_ds_add_column('adh_datasets', 'certified',
    "TINYINT NOT NULL DEFAULT 0 COMMENT '口径是否已人工认证; 正文/参数/字段变更即重置'");
CALL adh_ds_add_column('adh_datasets', 'approved_by',
    "VARCHAR(128) NULL COMMENT '认证人(服务端注入登录身份)'");

-- ----------------------------------------------------------------------------
-- 2. 数据搬迁：adh_sql_templates → adh_datasets（DROP 前保数据）
-- ----------------------------------------------------------------------------
-- name 用 template_name（业务可读且全局唯一语义一致）；已存在同名数据集则跳过
-- （幂等，且不覆盖人工在数据集侧改过的定义）。
INSERT INTO adh_datasets
    (name, description, source_type, object_key, datasource_id,
     sql_query, variables, field_config, owner_id, status)
SELECT t.template_name,
       t.description,
       'sql',
       '',
       t.datasource_id,
       t.sql_template,
       NULLIF(t.variables, ''),   -- 旧列是 TEXT：空串转 NULL（JSON 列不收空串）
       '[]',
       0,
       'active'
FROM adh_sql_templates t
WHERE t.template_name IS NOT NULL
  AND t.template_name <> ''
  -- 排序规则：adh_datasets 为 utf8mb4_0900_ai_ci、adh_sql_templates 为 utf8mb4_unicode_ci，
  -- 直接比较会报 1267 Illegal mix of collations，故显式统一到 0900_ai_ci。
  AND NOT EXISTS (SELECT 1 FROM adh_datasets d
                  WHERE d.name = t.template_name COLLATE utf8mb4_0900_ai_ci);

-- ----------------------------------------------------------------------------
-- 3. adh_ontology_bindings：template_ref → dataset_ref
-- ----------------------------------------------------------------------------
-- 语义升级：值从 adh_sql_templates.template_id 改为 adh_datasets.name。
-- 注意：旧列存的是 template_id（如 user_path_conversion_rate），而搬迁后的数据集
-- name 取 template_name（业务可读），因此需经模板表转一道才能对上（本段在 DROP 之前）。
-- 先加列、迁值（能对上的模板已搬成数据集），再拆旧列。
CALL adh_ds_add_column('adh_ontology_bindings', 'dataset_ref',
    "VARCHAR(128) NULL COMMENT 'bind_kind=dataset 时指向 adh_datasets.name(稳定身份)'");

UPDATE adh_ontology_bindings b
JOIN adh_sql_templates t ON t.template_id COLLATE utf8mb4_0900_ai_ci = b.template_ref
JOIN adh_datasets d ON d.name = t.template_name COLLATE utf8mb4_0900_ai_ci
SET b.dataset_ref = d.name
WHERE b.template_ref IS NOT NULL AND b.template_ref <> ''
  AND (b.dataset_ref IS NULL OR b.dataset_ref = '');

SET @has_old := (SELECT COUNT(*) FROM information_schema.columns
                 WHERE table_schema=DATABASE() AND table_name='adh_ontology_bindings'
                   AND column_name='template_ref');
SET @ddl := IF(@has_old > 0,
    'ALTER TABLE adh_ontology_bindings DROP COLUMN template_ref',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 4. 字模库退役 sql_template 分类
-- ----------------------------------------------------------------------------
-- 2 条 builtin 演示片段（sql_trend_daily / sql_top_n）是 `{{table}}` 形式的不可执行
-- 片段，用户自建 0 条；query_template 列的 params/bindTable 全库无消费方。
DELETE FROM adh_vis_components WHERE category = 'sql_template';

SET @has_qt := (SELECT COUNT(*) FROM information_schema.columns
                WHERE table_schema=DATABASE() AND table_name='adh_vis_components'
                  AND column_name='query_template');
SET @ddl := IF(@has_qt > 0,
    'ALTER TABLE adh_vis_components DROP COLUMN query_template',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- ----------------------------------------------------------------------------
-- 5. 拆表：adh_sql_templates（能力已并入 adh_datasets，代码/注释同步清理）
-- ----------------------------------------------------------------------------
-- 仅在表存在时拆；幂等可重复执行。
SET @has_tbl := (SELECT COUNT(*) FROM information_schema.tables
                 WHERE table_schema=DATABASE() AND table_name='adh_sql_templates');
SET @ddl := IF(@has_tbl > 0,
    'DROP TABLE adh_sql_templates',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

DROP PROCEDURE IF EXISTS adh_ds_add_column;

-- ============================================================================
-- 验收（手工跑一遍）：
--   SELECT name, source_type, CHAR_LENGTH(sql_query) FROM adh_datasets;
--     → 应包含原 adh_sql_templates 的 2 行（模板名即数据集名）
--   SHOW TABLES LIKE 'adh_sql_templates';           → 空
--   SHOW COLUMNS FROM adh_vis_components LIKE 'query_template';  → 空
--   SHOW COLUMNS FROM adh_ontology_bindings LIKE 'template_ref'; → 空
-- ============================================================================

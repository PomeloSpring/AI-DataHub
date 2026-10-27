-- 本体草案隔离：生效内容与待发布改动分列存储（2026-10-26 拍板的两段式）
--
-- 背景：save_draft 对 active 模型是「就地编辑 + 立即级联」，AS-BOT 一旦能写业务本体，
--       就会直接覆写生效版并触发级联，中间没有草案缓冲。
--       两段式要求：改动只落草案，publish 才生效并级联。
--
-- 设计：在 adh_ontology_models 上加草案列，不新建模型行（避免污染模型清单）。
--   draft_json_content  待发布的 canonical doc（NULL = 无待发布改动）
--   draft_base_stamp    草案基于哪份生效内容（发布时校验生效版是否已被别人改过）
--   draft_updated_at/by 谁在什么时候改的草案
--
-- 幂等：可重复执行（逐列查 INFORMATION_SCHEMA 后 PREPARE，MySQL 5.7/8.x 通用；
-- 直接 ALTER ADD COLUMN 重跑会报 Duplicate column，故不得裸写）。

DROP PROCEDURE IF EXISTS adh_add_col_if_missing;
CREATE PROCEDURE adh_add_col_if_missing(IN p_table VARCHAR(64), IN p_col VARCHAR(64), IN p_ddl TEXT)
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_table AND COLUMN_NAME = p_col) THEN
        SET @s = p_ddl;
        PREPARE st FROM @s;
        EXECUTE st;
        DEALLOCATE PREPARE st;
    END IF;
END;

CALL adh_add_col_if_missing('adh_ontology_models', 'draft_json_content',
    'ALTER TABLE adh_ontology_models ADD COLUMN draft_json_content MEDIUMTEXT NULL COMMENT ''待发布草案（canonical doc）；NULL=无待发布改动''');
CALL adh_add_col_if_missing('adh_ontology_models', 'draft_base_stamp',
    'ALTER TABLE adh_ontology_models ADD COLUMN draft_base_stamp VARCHAR(64) NULL COMMENT ''草案所基于的生效内容指纹，发布时校验是否已被他人改动''');
CALL adh_add_col_if_missing('adh_ontology_models', 'draft_updated_at',
    'ALTER TABLE adh_ontology_models ADD COLUMN draft_updated_at DATETIME NULL COMMENT ''草案最近修改时间''');
CALL adh_add_col_if_missing('adh_ontology_models', 'draft_updated_by',
    'ALTER TABLE adh_ontology_models ADD COLUMN draft_updated_by VARCHAR(128) NULL COMMENT ''草案最近修改人（服务端注入）''');

DROP PROCEDURE IF EXISTS adh_add_col_if_missing;

-- ── 核验（人工执行后确认，勿删）─────────────────────────────────
-- SHOW COLUMNS FROM adh_ontology_models LIKE 'draft%';
-- SELECT id, name, kind, status, (draft_json_content IS NOT NULL) AS has_draft FROM adh_ontology_models;

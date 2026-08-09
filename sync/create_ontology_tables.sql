-- AI-DataHub Ontology Model Tables
--
-- 本体建模（FDE 对象中心范式）：
--   adh_ontology_models  — 本体模型文档（JSON 事实源 + YAML/MD 派生，draft/active/archived）
--   adh_ontology_objects — 模型激活后展开的业务对象（逐对象 MD 段向量化，供 ontology_first 检索）
--
-- ═══════════════════════════════════════════════════════════════════
-- Part 1: 元数据库（MySQL，METADATA_DB_DATABASE，事实源）
-- Run: mysql -h <mysql_host> -P 3306 -u <user> -p <db> < create_ontology_tables.sql
--      （仅执行 Part 1 段；Part 2 为 Doris 语法）
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS adh_ontology_models (
    id              BIGINT NOT NULL PRIMARY KEY,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    name            VARCHAR(256) NOT NULL,
    status          VARCHAR(16)  NOT NULL DEFAULT 'draft',
    json_content    MEDIUMTEXT,
    yaml_content    MEDIUMTEXT,
    md_content      MEDIUMTEXT,
    object_count    INT          DEFAULT 0,
    created_by      VARCHAR(64)  DEFAULT '',
    created_at      DATETIME     NOT NULL,
    updated_at      DATETIME     NOT NULL,
    KEY idx_ontology_models_ds (datasource_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体模型（JSON 事实源 + YAML/MD 派生）';

CREATE TABLE IF NOT EXISTS adh_ontology_objects (
    id              BIGINT NOT NULL PRIMARY KEY,
    model_id        BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    object_key      VARCHAR(128) NOT NULL,
    display_name    VARCHAR(256) NOT NULL,
    aliases         VARCHAR(512) DEFAULT '',
    description     VARCHAR(1024) DEFAULT '',
    md_section      MEDIUMTEXT,
    is_active       TINYINT      DEFAULT 1,
    embedding       JSON,
    KEY idx_ontology_objects_model (model_id),
    KEY idx_ontology_objects_active (is_active, datasource_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='本体对象向量（md_section 为向量化文本）';

-- ═══════════════════════════════════════════════════════════════════
-- Part 2: 向量库（Doris，VECTOR_DB_DATABASE，ANN 检索镜像）
-- Run: mysql -h <doris_host> -P 9030 -u <user> -p <VECTOR_DB_DATABASE>
--      （在 mysql 交互会话中执行以下两条语句；与现有 adh_* 向量表同库）
-- ═══════════════════════════════════════════════════════════════════

-- 模型文档镜像（datamind 策略经元数据库读取，Doris 侧可选；此处不建）

CREATE TABLE IF NOT EXISTS adh_ontology_objects (
    id              BIGINT NOT NULL,
    model_id        BIGINT NOT NULL,
    datasource_id   BIGINT NOT NULL DEFAULT 0,
    object_key      VARCHAR(128) NOT NULL,
    display_name    VARCHAR(256) NOT NULL,
    aliases         VARCHAR(512) DEFAULT '',
    description     VARCHAR(1024) DEFAULT '',
    md_section      TEXT         DEFAULT '',
    is_active       TINYINT      DEFAULT 1,
    embedding       ARRAY<FLOAT> NOT NULL,
    INDEX idx_embedding (embedding) USING ANN PROPERTIES (
        "index_type" = "hnsw",
        "metric_type" = "l2_distance",
        "dim" = "768",
        "max_degree" = "32",
        "ef_construction" = "64"
    )
) ENGINE = OLAP
DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 4
PROPERTIES ("replication_num" = "1");
